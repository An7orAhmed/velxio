from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .runtime_builder import stm32_runtime_builder

logger = logging.getLogger(__name__)
Callback = Callable[[str, dict], Awaitable[None]]


@dataclass
class Stm32Session:
  board: str
  callback: Callback
  sensors: list[dict] = field(default_factory=list)
  bus_map: dict = field(default_factory=dict)


@dataclass
class Stm32Instance:
  process: asyncio.subprocess.Process
  session: Stm32Session
  firmware_b64: str
  machine: str
  stdout_task: Optional[asyncio.Task] = None
  stderr_task: Optional[asyncio.Task] = None
  uart_buffers: dict[int, bytearray] = field(default_factory=dict)
  uart_flush_tasks: dict[int, asyncio.Task] = field(default_factory=dict)


class Stm32OssManager:
  def __init__(self) -> None:
    self.sessions: dict[str, Stm32Session] = {}
    self.instances: dict[str, Stm32Instance] = {}
    self._locks: dict[str, asyncio.Lock] = {}

  def _lock(self, client_id: str) -> asyncio.Lock:
    return self._locks.setdefault(client_id, asyncio.Lock())

  def supports_board(self, board: str) -> bool:
    return board == 'stm32-blackpill-f401'

  def owns_client(self, client_id: str) -> bool:
    session = self.sessions.get(client_id)
    return session is not None and self.supports_board(session.board)

  def machine_for(self, board: str) -> str:
    if board != 'stm32-blackpill-f401':
      raise RuntimeError(f'Unsupported self-hosted STM32 board: {board}')
    machine = os.getenv('VELXIO_STM32_F401_MACHINE', '').strip()
    if not machine:
      raise RuntimeError(
        'No verified STM32F401 machine is available in the selected open QEMU runtime yet. '
        'Set VELXIO_STM32_F401_MACHINE only when using a PICSimLab-compatible runtime '
        'that actually implements STM32F401CE. Do not alias an F405 machine.'
      )
    return machine

  async def start_instance(
    self,
    client_id: str,
    board: str,
    callback: Callback,
    firmware_b64: str,
    sensors: Optional[list[dict]] = None,
    bus_map: Optional[dict] = None,
  ) -> None:
    if not self.supports_board(board):
      raise RuntimeError(f'Board {board} is not handled by the OSS STM32 backend')

    session = Stm32Session(
      board=board,
      callback=callback,
      sensors=list(sensors or []),
      bus_map=dict(bus_map or {}),
    )
    self.sessions[client_id] = session
    if not firmware_b64:
      await callback('system', {
        'event': 'stm32_session_ready',
        'message': 'STM32F401 session reserved; waiting for firmware.',
      })
      return
    await self._launch(client_id, session, firmware_b64)

  async def _launch(self, client_id: str, session: Stm32Session, firmware_b64: str) -> None:
    async with self._lock(client_id):
      await self._stop_worker_unlocked(client_id)
      runtime = await stm32_runtime_builder.ensure_runtime(session.callback)
      machine = self.machine_for(session.board)

      process = await asyncio.create_subprocess_exec(
        sys.executable,
        '-m',
        'app.services.stm32_worker',
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
      )
      instance = Stm32Instance(
        process=process,
        session=session,
        firmware_b64=firmware_b64,
        machine=machine,
      )
      self.instances[client_id] = instance
      await self._write(instance, {
        'lib_path': str(runtime.lib_path),
        'firmware_b64': firmware_b64,
        'machine': machine,
        'sensors': session.sensors,
      })
      instance.stdout_task = asyncio.create_task(self._read_stdout(client_id, instance))
      instance.stderr_task = asyncio.create_task(self._read_stderr(client_id, instance))

  async def stop_instance(self, client_id: str) -> None:
    self.sessions.pop(client_id, None)
    async with self._lock(client_id):
      await self._stop_worker_unlocked(client_id)
    self._locks.pop(client_id, None)

  async def _stop_worker_unlocked(self, client_id: str) -> None:
    instance = self.instances.pop(client_id, None)
    if instance is None:
      return
    try:
      if instance.process.returncode is None:
        await self._write(instance, {'cmd': 'stop'})
        try:
          await asyncio.wait_for(instance.process.wait(), timeout=6)
        except asyncio.TimeoutError:
          instance.process.terminate()
          try:
            await asyncio.wait_for(instance.process.wait(), timeout=2)
          except asyncio.TimeoutError:
            instance.process.kill()
            await instance.process.wait()
    except (BrokenPipeError, ConnectionResetError, ProcessLookupError):
      pass
    finally:
      current = asyncio.current_task()
      tasks = [instance.stdout_task, instance.stderr_task, *instance.uart_flush_tasks.values()]
      for task in tasks:
        if task and task is not current and not task.done():
          task.cancel()

  async def reload_firmware(self, client_id: str, firmware_b64: str) -> None:
    session = self.sessions.get(client_id)
    if session is None:
      raise RuntimeError('STM32 session is not reserved')
    if not firmware_b64:
      raise RuntimeError('STM32 firmware is empty')
    await self._launch(client_id, session, firmware_b64)

  async def set_pin(self, client_id: str, pin: int, state: int) -> None:
    await self._command(client_id, {'cmd': 'set_pin', 'pin': int(pin), 'value': int(state) & 1})

  async def send_serial(self, client_id: str, data: Any, uart: int = 0) -> None:
    if isinstance(data, str):
      raw = data.encode()
    elif isinstance(data, list):
      raw = bytes(int(value) & 0xFF for value in data)
    elif isinstance(data, (bytes, bytearray)):
      raw = bytes(data)
    else:
      raw = b''
    if raw:
      await self._command(client_id, {
        'cmd': 'uart_send',
        'uart': int(uart),
        'data': base64.b64encode(raw).decode('ascii'),
      })

  async def sensor_attach(self, client_id: str, data: dict) -> None:
    session = self.sessions.get(client_id)
    if session is not None:
      pin = data.get('pin')
      index = next((i for i, sensor in enumerate(session.sensors) if sensor.get('pin') == pin), -1)
      if index >= 0:
        session.sensors[index] = dict(data)
      else:
        session.sensors.append(dict(data))
    if client_id in self.instances:
      await self._command(client_id, {'cmd': 'sensor_attach', **data})

  async def sensor_update(self, client_id: str, data: dict) -> None:
    session = self.sessions.get(client_id)
    if session is not None:
      pin = data.get('pin')
      index = next((i for i, sensor in enumerate(session.sensors) if sensor.get('pin') == pin), -1)
      if index >= 0:
        session.sensors[index] = {**session.sensors[index], **data}
    if client_id in self.instances:
      await self._command(client_id, {'cmd': 'sensor_update', **data})

  async def sensor_detach(self, client_id: str, data: dict) -> None:
    session = self.sessions.get(client_id)
    if session is not None:
      pin = data.get('pin')
      session.sensors = [sensor for sensor in session.sensors if sensor.get('pin') != pin]
    if client_id in self.instances:
      await self._command(client_id, {'cmd': 'sensor_detach', **data})

  async def _command(self, client_id: str, payload: dict) -> None:
    instance = self.instances.get(client_id)
    if instance is None:
      return
    await self._write(instance, payload)

  async def _write(self, instance: Stm32Instance, payload: dict) -> None:
    if instance.process.stdin is None or instance.process.returncode is not None:
      raise RuntimeError('STM32 worker is not available')
    instance.process.stdin.write((json.dumps(payload) + '\n').encode())
    await instance.process.stdin.drain()

  async def _read_stdout(self, client_id: str, instance: Stm32Instance) -> None:
    if instance.process.stdout is None:
      return
    try:
      while True:
        raw = await instance.process.stdout.readline()
        if not raw:
          break
        try:
          event = json.loads(raw)
        except json.JSONDecodeError:
          continue
        event_type = str(event.pop('type', ''))
        if event_type == 'uart_tx':
          await self._buffer_uart(instance, int(event.get('uart', 0)), int(event.get('byte', 0)))
        elif event_type:
          await instance.session.callback(event_type, event)
    except asyncio.CancelledError:
      raise
    except Exception as exc:
      logger.exception('STM32 stdout reader failed for %s', client_id)
      await instance.session.callback('error', {'message': f'STM32 worker output failed: {exc}'})
    finally:
      if self.instances.get(client_id) is instance and instance.process.returncode is not None:
        self.instances.pop(client_id, None)

  async def _buffer_uart(self, instance: Stm32Instance, uart: int, byte_value: int) -> None:
    buffer = instance.uart_buffers.setdefault(uart, bytearray())
    buffer.append(byte_value & 0xFF)
    if uart not in instance.uart_flush_tasks or instance.uart_flush_tasks[uart].done():
      instance.uart_flush_tasks[uart] = asyncio.create_task(self._flush_uart(instance, uart))

  async def _flush_uart(self, instance: Stm32Instance, uart: int) -> None:
    await asyncio.sleep(0.01)
    buffer = instance.uart_buffers.setdefault(uart, bytearray())
    if not buffer:
      return
    raw = bytes(buffer)
    buffer.clear()
    await instance.session.callback('serial_output', {
      'uart': uart,
      'data': raw.decode('utf-8', errors='replace'),
      'b64': base64.b64encode(raw).decode('ascii'),
    })

  async def _read_stderr(self, client_id: str, instance: Stm32Instance) -> None:
    if instance.process.stderr is None:
      return
    try:
      while True:
        raw = await instance.process.stderr.readline()
        if not raw:
          break
        logger.debug('[stm32:%s] %s', client_id, raw.decode(errors='replace').rstrip())
    except asyncio.CancelledError:
      raise


stm32_oss_manager = Stm32OssManager()
