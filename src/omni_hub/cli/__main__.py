"""Allow ``python -m omni_hub.cli`` to invoke the CLI."""

from . import main

raise SystemExit(main())
