from __future__ import annotations

import logging
from typing import Any

from app.core.hooks import register_ws_sim_disconnect, register_ws_sim_handler

from .manager import stm32_oss_manager
from .runtime_builder import stm32_runtime_builder

logger = logging.getLogger(__name__)
_registered = False


async def _handle_stm32(
  websocket: Any,
  client_id: str,
  msg_type: str,
  msg_data: dict,
  callback: Any,
) -> bool:
  del websocket

  if msg_type == 'start_stm32':
    board = str(msg_data.get('board') or '')
    if not stm32_oss_manager.supports_board(board):
      return False
    try:
      await stm32_oss_manager.start_instance(
        client_id=client_id,
        board=board,
        callback=callback,
        firmware_b64=str(msg_data.get('firmware_b64') or ''),
        sensors=msg_data.get('sensors') if isinstance(msg_data.get('sensors'), list) else [],
        bus_map=msg_data.get('bus_map') if isinstance(msg_data.get('bus_map'), dict) else {},
      )
    except Exception as exc:
      logger.exception('Could not start self-hosted STM32 instance')
      await callback('error', {
        'code': 'STM32_OSS_START_FAILED',
        'message': str(exc),
      })
    return True

  instance = stm32_oss_manager.instances.get(client_id)
  owns_client = instance is not None and stm32_oss_manager.supports_board(instance.board)

  if msg_type == 'stm32_cleanup_build':
    result = await stm32_runtime_builder.cleanup_build_files()
    await callback('system', {
      'event': 'stm32_runtime_cleanup_done',
      **result,
    })
    return True

  if not owns_client:
    return False

  try:
    if msg_type == 'stop_stm32':
      await stm32_oss_manager.stop_instance(client_id)
    elif msg_type == 'stm32_load_firmware':
      firmware_b64 = str(msg_data.get('firmware_b64') or '')
      if firmware_b64:
        await stm32_oss_manager.reload_firmware(client_id, firmware_b64)
    elif msg_type == 'stm32_gpio_in':
      await stm32_oss_manager.set_pin(
        client_id,
        int(msg_data.get('pin', 0)),
        int(msg_data.get('state', 0)),
      )
    elif msg_type == 'stm32_serial_input':
      data = msg_data.get('bytes', msg_data.get('data', []))
      await stm32_oss_manager.send_serial(client_id, data, int(msg_data.get('uart', 0)))
    elif msg_type == 'stm32_sensor_attach':
      await stm32_oss_manager.sensor_attach(client_id, msg_data)
    elif msg_type == 'stm32_sensor_update':
      await stm32_oss_manager.sensor_update(client_id, msg_data)
    elif msg_type == 'stm32_sensor_detach':
      await stm32_oss_manager.sensor_detach(client_id, msg_data)
    elif msg_type in ('stm32_bus_map', 'stm32_bus_attrs'):
      pass
    else:
      return False
  except Exception as exc:
    logger.exception('STM32 OSS message failed: %s', msg_type)
    await callback('error', {
      'code': 'STM32_OSS_COMMAND_FAILED',
      'message': str(exc),
    })
  return True


async def _disconnect(client_id: str) -> None:
  if client_id in stm32_oss_manager.instances:
    await stm32_oss_manager.stop_instance(client_id)


def register_stm32_oss_hooks() -> None:
  global _registered
  if _registered:
    return
  register_ws_sim_handler(_handle_stm32)
  register_ws_sim_disconnect(_disconnect)
  _registered = True
