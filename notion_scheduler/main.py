import argparse
import datetime
import logging
import os
import sys
import time
from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, Generator, Optional, List

import yaml
from dateutil import rrule
from durations import Duration
from notion.block.collection.basic import CollectionRowBlock
from notion.block.collection.common import NotionDate
from notion.client import NotionClient
from recurrent.event_parser import RecurringEvent

LOGGING_FORMAT = "%(levelname)s: %(message)s"


def expanded_path(x: str) -> str:
    return os.path.expanduser(os.path.expandvars(x))


class LogLevel(Enum):
    NORMAL = 'normal'
    VERBOSE = 'verbose'
    QUIET = 'quiet'

    def into_logging_level(self) -> int:
        if self == LogLevel.NORMAL:
            return logging.WARNING
        if self == LogLevel.VERBOSE:
            return logging.INFO
        if self == LogLevel.QUIET:
            return logging.CRITICAL
        assert False


DEFAULT_CONFIG_FILENAME = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", "$HOME/.config"),
    "notion_scheduler/config.yml")


@dataclass
class Settings:
    config_filename: str = DEFAULT_CONFIG_FILENAME
    log_level: LogLevel = LogLevel.NORMAL
    dry_run: bool = False
    delete_rescheduled: bool = False
    append: bool = False


@dataclass
class Config:
    tasks_collection_url: str
    scheduled_collection_url: str
    token_v2: str
    properties_to_sync: List[str]
    scheduled_tag: str
    rescheduled_tag: str
    status_property: Optional[str]
    tags_property: str
    status_before_today: str
    status_after_today: str


def parse_args_into(settings: Settings) -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        help="don't modify anything, just show what would be done",
        action="store_true",
    )
    parser.add_argument(
        "--log-level",
        dest="log_level",
        type=LogLevel,
        choices=LogLevel,
        default=LogLevel.NORMAL,
        help="the logging verbosity level to use",
    )
    parser.add_argument(
        "--config-filename",
        dest="config_filename",
        help=
        f"the config file to use (default is {DEFAULT_CONFIG_FILENAME}, '-' for stdin)",
    )
    parser.add_argument(
        "--delete-rescheduled",
        dest="delete_rescheduled",
        help="also delete 'Rescheduled' events",
        action="store_true",
    )
    parser.add_argument(
        "--append",
        dest="append",
        help="only append events, do not delete existing 'Scheduled'",
        action="store_true",
    )

    parser.parse_args(namespace=settings)
    settings.config_filename = expanded_path(settings.config_filename)


def main() -> None:
    settings = Settings()
    parse_args_into(settings)

    # set logging level from arguments
    logging.basicConfig(level=settings.log_level.into_logging_level(),
                        format=LOGGING_FORMAT)

    config = parse_config(settings)

    if settings.dry_run:
        logging.info("Dry run active, no modifications will be made")
    run_scheduler(settings, config)


def parse_config(settings: Settings) -> Config:
    if settings.config_filename == '-':
        config_file = sys.stdin
    else:
        config_file = open(settings.config_filename)
    config = yaml.safe_load(config_file.read())
    config_file.close()
    return Config(**config)


def parse_reminder(reminder_str: str) -> Dict[str, Any]:
    reminder = Duration(reminder_str).parsed_durations[0]
    return {
        'value': int(reminder.value),
        'unit': reminder.scale.representation.long_singular
    }


def create_entries(
    settings: Settings,
    config: Config,
    spec_row: CollectionRowBlock,
) -> Generator[Dict[str, Any], None, None]:
    r = RecurringEvent(now_date=spec_row.start_date.start)
    times = r.parse(spec_row.recurrence)
    rr = rrule.rrulestr(r.get_RFC_rrule(), dtstart=spec_row.start_date.start)

    if spec_row.not_on != '-':
        not_r = RecurringEvent(now_date=spec_row.start_date.start)
        not_times = not_r.parse(spec_row.not_on)
        not_dates = {
            d.date()
            for d in rrule.rrulestr(
                not_r.get_RFC_rrule(),
                dtstart=spec_row.start_date.start,
            )
        }

    for dt in rr:
        if spec_row.not_on != '-' and dt.date() in not_dates:
            continue

        to_insert = {
            key: spec_row.get_property(key)
            for key in config.properties_to_sync
        }
        to_insert['title'] = spec_row.title
        if config.tags_property in to_insert:
            to_insert[config.tags_property].append(config.scheduled_tag)
        if config.status_property:
            to_insert[
                config.status_property] = config.status_after_today if dt.date(
                ) >= datetime.date.today() else config.status_before_today

        reminder = None
        if spec_row.reminder:
            reminder = parse_reminder(spec_row.reminder)

        if spec_row.include_time:
            if spec_row.duration:
                duration = datetime.timedelta(
                    minutes=Duration(spec_row.duration).to_minutes())
                to_insert[spec_row.date_field] = NotionDate(dt,
                                                            dt + duration,
                                                            reminder=reminder)
            else:
                to_insert[spec_row.date_field] = NotionDate(dt,
                                                            reminder=reminder)
        else:
            to_insert[spec_row.date_field] = NotionDate(dt.date(),
                                                        reminder=reminder)

        if not settings.dry_run:
            yield to_insert
        logging.info(
            f"Added row '{to_insert.get('title', 'Untitled')}' for {dt:%Y-%m-%d}"
        )


def run_scheduler(settings: Settings, config: Config) -> None:
    client = NotionClient(token_v2=config.token_v2)
    todo_col = client.get_collection_view(config.tasks_collection_url,
                                          force_refresh=True).collection
    scheduled_col = client.get_collection_view(config.scheduled_collection_url,
                                               force_refresh=True).collection

    def tag_filter(tag: str) -> Dict[str, Any]:
        return {
            'property': config.tags_property,
            'filter': {
                'operator': 'enum_contains',
                'value': {
                    'type': 'exact',
                    'value': tag
                },
            }
        }

    scheduled_filter: Dict[str, Any] = {"filters": [], "operator": "or"}
    if not settings.append:
        scheduled_filter['filters'].append(tag_filter(config.scheduled_tag))
    if settings.delete_rescheduled:
        scheduled_filter['filters'].append(tag_filter(config.rescheduled_tag))

    # remove all scheduled
    for row in (CollectionRowBlock(client, row.id)
                for row in todo_col.get_rows(filter=scheduled_filter)):
        title = row.title
        if not settings.dry_run:
            row.remove()
        logging.info(f"Removed pre-existing scheduled row '{title}'")

    # add new
    for row in (CollectionRowBlock(client, row.id)
                for row in scheduled_col.get_rows()):
        row.refresh()
        for entry in create_entries(settings, config, row):
            todo_col.add_row(**entry)
