import { useProjectStore } from '../store/useProjectStore';
import { useEditorStore } from '../store/useEditorStore';
import { useSimulatorStore, DEFAULT_BOARD_POSITION } from '../store/useSimulatorStore';
import type { PlatformIOImport } from './platformioProject';

export function applyPlatformIOImport(project: PlatformIOImport): void {
  useProjectStore.getState().clearCurrentProject();

  const simulator = useSimulatorStore.getState();
  simulator.stopSimulation();
  simulator.setComponents([]);
  simulator.setWires([]);

  for (const board of [...simulator.boards]) {
    useSimulatorStore.getState().removeBoard(board.id);
  }

  const editor = useEditorStore.getState();
  for (const groupId of Object.keys(editor.fileGroups)) {
    useEditorStore.getState().deleteFileGroup(groupId);
  }

  const boardId = useSimulatorStore.getState().addBoard(
    project.boardType,
    DEFAULT_BOARD_POSITION.x,
    DEFAULT_BOARD_POSITION.y,
  );
  useSimulatorStore.getState().setActiveBoardId(boardId);

  const board = useSimulatorStore.getState().boards.find((item) => item.id === boardId);
  if (!board) throw new Error('Could not create the board for this PlatformIO project.');

  useEditorStore.getState().setActiveGroup(board.activeFileGroupId);
  useEditorStore.getState().loadFiles(project.files);
}
