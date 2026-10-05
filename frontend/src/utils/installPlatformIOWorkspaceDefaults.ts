import { useEditorStore } from '../store/useEditorStore';
import {
  migrateSingleSketchWorkspace,
  platformIOTargetForBoard,
  platformIOWorkspaceFiles,
} from './platformioWorkspace';

let installed = false;

function boardKindFromGroupId(groupId: string): string | undefined {
  if (!groupId.startsWith('group-') || groupId.startsWith('group-chip-')) return undefined;
  const raw = groupId.slice('group-'.length);
  if (platformIOTargetForBoard(raw)) return raw;

  // Second and later instances are named <kind>-2, <kind>-3, ... . Try the
  // exact kind first because several real board kinds themselves end in a
  // digit (esp32-c3, stm32f4 variants, etc.).
  const match = raw.match(/^(.*)-(\d+)$/);
  if (match && platformIOTargetForBoard(match[1])) return match[1];
  return undefined;
}

/**
 * Replace the Arduino-sketch defaults with PlatformIO-native workspaces.
 *
 * This intentionally wraps createFileGroup/loadFiles but NOT replaceFileGroups:
 * loading an old saved .vlx therefore preserves its .ino files exactly, while
 * newly created boards, starter examples and the pristine editor use
 * platformio.ini + src/main.cpp. The backend keeps a legacy compile fallback.
 */
export function installPlatformIOWorkspaceDefaults(): void {
  if (installed) return;
  installed = true;

  const initial = useEditorStore.getState();
  const originalCreateFileGroup = initial.createFileGroup;
  const originalLoadFiles = initial.loadFiles;

  useEditorStore.setState({
    createFileGroup: (groupId, languageModeOrFiles) => {
      const kind = boardKindFromGroupId(groupId);
      if (kind) {
        if (Array.isArray(languageModeOrFiles)) {
          return originalCreateFileGroup(
            groupId,
            migrateSingleSketchWorkspace(kind, languageModeOrFiles),
          );
        }
        if (languageModeOrFiles === undefined || languageModeOrFiles === 'arduino') {
          const files = platformIOWorkspaceFiles(kind);
          if (files) return originalCreateFileGroup(groupId, files);
        }
      }
      return originalCreateFileGroup(groupId, languageModeOrFiles);
    },

    loadFiles: (files) => {
      const state = useEditorStore.getState();
      const kind = boardKindFromGroupId(state.activeGroupId);
      return originalLoadFiles(kind ? migrateSingleSketchWorkspace(kind, files) : files);
    },
  });

  // Zustand's initial state is constructed before this installer runs, so the
  // default Uno group already contains sketch.ino. Migrate only that pristine
  // built-in seed. User-loaded/saved workspaces are restored later through
  // replaceFileGroups and are deliberately not rewritten.
  const state = useEditorStore.getState();
  const files = state.getGroupFiles('group-arduino-uno');
  if (
    state.activeGroupId === 'group-arduino-uno' &&
    files.length === 1 &&
    files[0].name === 'sketch.ino' &&
    !files[0].modified
  ) {
    state.setActiveGroup('group-arduino-uno');
    state.loadFiles(files.map((f) => ({ name: f.name, content: f.content })));
  }
}
