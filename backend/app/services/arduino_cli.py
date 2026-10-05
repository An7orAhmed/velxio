from app.services.platformio import (
  PlatformIOService,
  _EXTRA_CORES,
  _extra_core_for_fqbn,
  _has_prelude,
  _looks_like_missing_header,
  _strip_comments,
  annotate_build_stderr,
  humanize_cli_error,
  register_extra_core,
)


class ArduinoCLIService(PlatformIOService):
  pass
