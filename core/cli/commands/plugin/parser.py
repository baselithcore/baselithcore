"""
Parser registration for plugin commands.
"""


def register_parser(subparsers, formatter_class):
    """Register 'plugin' command parser."""
    plugin_parser = subparsers.add_parser(
        "plugin",
        help="Manage framework plugins",
        description="Extend the core framework with plugins, custom agents, tools, and UI widgets.",
        formatter_class=formatter_class,
    )
    plugin_subparsers = plugin_parser.add_subparsers(
        dest="plugin_command", title="Available Plugin Actions"
    )

    # ─── Scaffolding ───────────────────────────────────────
    create_plugin = plugin_subparsers.add_parser(
        "create",
        help="Scaffold a new plugin extension",
        description="Generate the boilerplate for a new plugin following framework best practices.",
        formatter_class=formatter_class,
    )
    create_plugin.add_argument(
        "name", nargs="?", default="", help="Name of the plugin directory"
    )
    create_plugin.add_argument(
        "--type",
        choices=["agent", "router", "graph"],
        default="agent",
        help="Architectural type of the plugin",
    )
    create_plugin.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Run interactive creation wizard",
    )
    create_plugin.add_argument(
        "--no-register",
        action="store_true",
        help="Create plugin files without enabling it in configs/plugins.yaml",
    )

    add_plugin = plugin_subparsers.add_parser(
        "add",
        help="Clone and enable a Baselith plugin from Git",
        description=(
            "Clone a Git plugin into plugins/, validate it, enable it, "
            "and check dependencies."
        ),
        formatter_class=formatter_class,
    )
    add_plugin.add_argument("source", help="Git URL or local Git source to clone")
    add_plugin.add_argument(
        "--name",
        default=None,
        help="Plugin folder name under plugins/ (default: derived from repo name)",
    )
    add_plugin.add_argument("--ref", default=None, help="Branch, tag, or ref to clone")
    add_plugin.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing clean plugin directory",
    )
    add_plugin.add_argument(
        "--install-deps",
        action="store_true",
        help="Install missing Python dependencies into the current Python environment",
    )
    add_plugin.add_argument(
        "--docker",
        action="store_true",
        help="Install/build the plugin into the Docker core runtime",
    )

    sync_plugin = plugin_subparsers.add_parser(
        "sync",
        help="Reconcile local plugins with the Docker core runtime",
        description=(
            "Rebuild frontend assets, Python plugin requirements, and the Docker "
            "API runtime for enabled local plugins."
        ),
        formatter_class=formatter_class,
    )
    sync_plugin.add_argument(
        "--docker",
        action="store_true",
        help="Sync enabled plugins into the Docker core runtime",
    )

    # ─── Local Management ──────────────────────────────────
    plugin_subparsers.add_parser(
        "list",
        help="Display all currently installed and active plugins",
        formatter_class=formatter_class,
    )

    status_plugin = plugin_subparsers.add_parser(
        "status",
        help="Show detailed health, config alignment, and readiness of local plugins",
        formatter_class=formatter_class,
    )
    status_plugin.add_argument("--name", help="Specific plugin to check", default=None)

    info_local = plugin_subparsers.add_parser(
        "info",
        help="Examine detailed metadata for a local plugin",
        formatter_class=formatter_class,
    )
    info_local.add_argument("name", help="Name of the local plugin")

    delete_local = plugin_subparsers.add_parser(
        "delete",
        help="Delete a local plugin directory",
        formatter_class=formatter_class,
    )
    delete_local.add_argument("name", help="Name of the local plugin")
    delete_local.add_argument(
        "--force", action="store_true", help="Skip confirmation prompt"
    )

    disable_local = plugin_subparsers.add_parser(
        "disable",
        help="Disable a local plugin (prevents loading)",
        formatter_class=formatter_class,
    )
    disable_local.add_argument(
        "name", nargs="?", default="", help="Name of the local plugin"
    )
    disable_local.add_argument(
        "--all",
        action="store_true",
        dest="all_plugins",
        help="Disable all installed plugins",
    )

    enable_local = plugin_subparsers.add_parser(
        "enable",
        help="Enable a disabled local plugin",
        formatter_class=formatter_class,
    )
    enable_local.add_argument(
        "name", nargs="?", default="", help="Name of the local plugin"
    )
    enable_local.add_argument(
        "--all",
        action="store_true",
        dest="all_plugins",
        help="Enable all disabled plugins",
    )

    export_manifest = plugin_subparsers.add_parser(
        "export-manifest",
        help="Export metadata as a manifest.json file",
        formatter_class=formatter_class,
    )
    export_manifest.add_argument("name", help="Name of the local plugin")

    validate_local = plugin_subparsers.add_parser(
        "validate",
        help="Validate syntax, manifest, schema, dependencies, and env vars",
        formatter_class=formatter_class,
    )
    validate_local.add_argument("name", help="Name of the local plugin")

    schema_init = plugin_subparsers.add_parser(
        "schema-init",
        help="Create every enabled plugin's database schema (deploy step)",
        description=(
            "Run each enabled plugin's init_schema() as the table owner, "
            "before the application starts. The serving process then needs no "
            "DDL, which is what lets it connect as a least-privilege role and "
            "have row-level security actually apply to it."
        ),
        formatter_class=formatter_class,
    )
    schema_init.add_argument(
        "--plugin",
        dest="schema_plugin",
        default=None,
        help="Restrict the run to one plugin, by registry name",
    )

    sign_plugin = plugin_subparsers.add_parser(
        "sign",
        help="Compute the executable-surface SHA-256 and write it into manifest.integrity_sha256",
        formatter_class=formatter_class,
    )
    sign_plugin.add_argument("path", help="Path to the local plugin directory")
    sign_plugin.add_argument(
        "--check",
        action="store_true",
        help="Compute and print the hash without modifying the manifest",
    )

    # ─── Dependency Management ─────────────────────────────
    deps_parser = plugin_subparsers.add_parser(
        "deps",
        help="Manage plugin dependencies",
        description="Check and install Python, plugin, and resource dependencies.",
        formatter_class=formatter_class,
    )
    deps_subparsers = deps_parser.add_subparsers(
        dest="deps_command", title="Dependency Commands"
    )

    deps_check = deps_subparsers.add_parser(
        "check",
        help="Verify all declared dependencies for a plugin",
        formatter_class=formatter_class,
    )
    deps_check.add_argument("name", nargs="?", default="", help="Plugin name to check")
    deps_check.add_argument(
        "--all",
        action="store_true",
        dest="all_plugins",
        help="Check dependencies for all local plugins",
    )
    deps_check.add_argument(
        "--python-only",
        action="store_true",
        help="Only check Python package dependencies",
    )

    deps_install = deps_subparsers.add_parser(
        "install",
        help="Install missing Python dependencies for a plugin",
        formatter_class=formatter_class,
    )
    deps_install.add_argument("name", nargs="?", default="", help="Plugin name")
    deps_install.add_argument(
        "--all",
        action="store_true",
        dest="all_plugins",
        help="Install missing Python dependencies for all local plugins",
    )
    deps_install.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )
    deps_install.add_argument(
        "--dry-run",
        action="store_true",
        help="Show missing Python packages without installing them",
    )

    # ─── Configuration ─────────────────────────────────────
    config_parser = plugin_subparsers.add_parser(
        "config",
        help="Manage plugin configuration (plugins.yaml)",
        description="View and edit plugin configuration in configs/plugins.yaml.",
        formatter_class=formatter_class,
    )
    config_subparsers = config_parser.add_subparsers(
        dest="config_command", title="Config Commands"
    )

    config_show = config_subparsers.add_parser(
        "show",
        help="Display plugin configuration",
        formatter_class=formatter_class,
    )
    config_show.add_argument(
        "name", nargs="?", default=None, help="Plugin name (optional)"
    )

    config_set = config_subparsers.add_parser(
        "set",
        help="Set a configuration key for a plugin",
        formatter_class=formatter_class,
    )
    config_set.add_argument("name", help="Plugin name")
    config_set.add_argument("key", help="Configuration key")
    config_set.add_argument("value", help="Value to set")

    config_get = config_subparsers.add_parser(
        "get",
        help="Get a specific configuration value",
        formatter_class=formatter_class,
    )
    config_get.add_argument("name", help="Plugin name")
    config_get.add_argument("key", help="Configuration key")

    config_reset = config_subparsers.add_parser(
        "reset",
        help="Reset plugin configuration to defaults",
        formatter_class=formatter_class,
    )
    config_reset.add_argument("name", help="Plugin name")

    # ─── Logs ──────────────────────────────────────────────
    logs_parser = plugin_subparsers.add_parser(
        "logs",
        help="View runtime logs filtered by plugin",
        description="Display log entries from logs/ filtered by plugin name and level.",
        formatter_class=formatter_class,
    )
    logs_parser.add_argument("name", help="Plugin name to filter by")
    logs_parser.add_argument(
        "-n",
        "--lines",
        type=int,
        default=50,
        help="Maximum number of log lines (default: 50)",
    )
    logs_parser.add_argument(
        "-l",
        "--level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=None,
        help="Minimum log level filter",
    )

    # ─── Dependency Tree ───────────────────────────────────
    tree_parser = plugin_subparsers.add_parser(
        "tree",
        help="Display inter-plugin dependency tree",
        description="Visualize plugin dependency graph with satisfaction status.",
        formatter_class=formatter_class,
    )
    tree_parser.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Plugin name (optional, shows all if omitted)",
    )

    # ─── Marketplace ───────────────────────────────────────
    marketplace_parser = plugin_subparsers.add_parser(
        "marketplace",
        help="Interact with the Baselith Plugin Marketplace",
        description="Search, install, and manage plugins from the remote marketplace.",
        formatter_class=formatter_class,
    )

    market_subparsers = marketplace_parser.add_subparsers(
        dest="marketplace_command", title="Marketplace Commands"
    )

    list_plugins = market_subparsers.add_parser(
        "list",
        help="List all plugins available in the Baselith Marketplace",
        formatter_class=formatter_class,
    )
    list_plugins.add_argument(
        "--category", default="all", help="Filter by category (default: all)"
    )
    list_plugins.add_argument(
        "--refresh", action="store_true", help="Bypass cache and force refresh"
    )

    search_plugin = market_subparsers.add_parser(
        "search",
        help="Browse the Baselith Marketplace for new extensions",
        formatter_class=formatter_class,
    )
    search_plugin.add_argument("query", help="Keywords to search for")
    search_plugin.add_argument(
        "--category", default="all", help="Filter by category (default: all)"
    )

    info_plugin_market = market_subparsers.add_parser(
        "info",
        help="Examine detailed metadata for a specific marketplace plugin",
        formatter_class=formatter_class,
    )
    info_plugin_market.add_argument("plugin_id", help="Unique identifier of the plugin")

    install_plugin = market_subparsers.add_parser(
        "install",
        help="Download and integrate a plugin into your system",
        formatter_class=formatter_class,
    )
    install_plugin.add_argument("plugin_id", help="Plugin identifier to download")
    install_plugin.add_argument("--version", help="Fetch a specific version tag")
    install_plugin.add_argument(
        "--force", action="store_true", help="Overwrite existing files if any"
    )

    uninstall_plugin = market_subparsers.add_parser(
        "uninstall",
        help="Safely remove a plugin and its associated assets",
        formatter_class=formatter_class,
    )
    uninstall_plugin.add_argument(
        "plugin_id", help="Name or ID of the plugin to remove"
    )

    update_plugin = market_subparsers.add_parser(
        "update",
        help="Sync an installed plugin with the latest marketplace version",
        formatter_class=formatter_class,
    )
    update_plugin.add_argument("plugin_id", help="Plugin ID to update")

    login_market = market_subparsers.add_parser(
        "login",
        help="Authenticate for publishing (exchange a GitHub token, or paste a JWT/API key)",
        formatter_class=formatter_class,
    )
    login_market.add_argument(
        "--github-token",
        help="A GitHub token (PAT) to exchange for a marketplace session token",
    )

    market_subparsers.add_parser(
        "logout",
        help="Remove cached marketplace API key",
        formatter_class=formatter_class,
    )

    market_subparsers.add_parser(
        "identity",
        help="Show your marketplace identity and token status",
        formatter_class=formatter_class,
    )

    publish_plugin = market_subparsers.add_parser(
        "publish",
        help="Package and ship a local plugin to the central hub",
        formatter_class=formatter_class,
    )
    publish_plugin.add_argument(
        "path", help="Local directory path of the plugin to publish"
    )
    publish_plugin.add_argument(
        "--key", help="Optional authentication key (or use MARKETPLACE_API_KEY)"
    )

    return plugin_parser
