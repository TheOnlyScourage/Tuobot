#!/usr/bin/env python3
"""
Startup wrapper for Railway deployment.
Generates config.cfg from environment variables, then launches the bot.
"""
import os
import sys

# Every value goes in as an escaped Python string literal ({x!r}), and
# core/config.py converts each to its real type (int / list). Values used to be
# pasted in raw, so an empty DC_OWNER_ID (SyntaxError), a quote in STATUS or
# HELP, or a backslash in the database password broke config.cfg and
# crash-looped the boot.
TEMPLATE = '''# -*- coding: utf-8 -*-
# Auto-generated from environment variables for Railway deployment

DC_BOT_TOKEN = {dc_bot_token!r}
DC_OWNER_ID = {dc_owner_id!r}
DC_SLASH_SERVERS = {dc_slash_servers!r}


DB_URI = {db_uri!r}
LOG_LEVEL = {log_level!r}
COMMANDS_URL = {commands_url!r}
HELP = {help_text!r}
STATUS = {status!r}
'''


def build_db_uri():
    """Build DB_URI from Railway's MySQL plugin variables or explicit DB_URI."""
    if os.environ.get("DB_URI"):
        return os.environ["DB_URI"]

    # Railway MySQL plugin provides these variables
    host = os.environ.get("MYSQLHOST") or os.environ.get("MYSQL_HOST", "")
    port = os.environ.get("MYSQLPORT") or os.environ.get("MYSQL_PORT", "3306")
    user = os.environ.get("MYSQLUSER") or os.environ.get("MYSQL_USER", "")
    password = os.environ.get("MYSQLPASSWORD") or os.environ.get("MYSQL_PASSWORD", "")
    database = os.environ.get("MYSQLDATABASE") or os.environ.get("MYSQL_DATABASE", "")

    if host and user and database:
        return f"mysql://{user}:{password}@{host}:{port}/{database}"

    return ""


def main():
    db_uri = build_db_uri()
    if not db_uri:
        print("ERROR: No database configured. Set DB_URI or add a MySQL service in Railway.")
        sys.exit(1)

    token = os.environ.get("DC_BOT_TOKEN", "")
    if not token:
        print("ERROR: DC_BOT_TOKEN environment variable is required.")
        sys.exit(1)

    owner_id = os.environ.get("DC_OWNER_ID", "0")
    if owner_id == "0":
        print("WARNING: DC_OWNER_ID not set. Bot owner commands won't work.")

    config_content = TEMPLATE.format(
        dc_bot_token=token,
        dc_owner_id=owner_id,
        dc_slash_servers=os.environ.get("DC_SLASH_SERVERS", ""),
        db_uri=db_uri,
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        commands_url=os.environ.get("COMMANDS_URL",
            "https://github.com/TheOnlyScourage/Tuobot/blob/main/README.md"),
        help_text=os.environ.get("HELP",
            "Tuobot is a Discord bot for organising Q6 (Quidditch 6v6) pickup games."),
        status=os.environ.get("STATUS", "Tuobot2.0"),
    )

    with open("config.cfg", "w") as f:
        f.write(config_content)

    print("config.cfg generated from environment variables.")
    print(f"Database: {db_uri.split('@')[-1] if '@' in db_uri else '(configured)'}")

    # Launch the bot
    os.execvp(sys.executable, [sys.executable, "PUBobot2.py"])


if __name__ == "__main__":
    main()
