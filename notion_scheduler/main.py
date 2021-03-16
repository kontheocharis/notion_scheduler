import argparse
import datetime
import logging
import os
import sys
import time
from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, Generator, Optional, List, Tuple
from collections import defaultdict

import yaml
from dateutil import rrule
from durations import Duration
from notion.block.collection.basic import CollectionRowBlock
from notion.block.collection.common import NotionDate
from notion.client import NotionClient, CollectionBlock
import notion.operations
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


class Context:
    settings: Settings
    config: Config
    todo_col: CollectionBlock
    scheduled_col: CollectionBlock
    client: NotionClient

    def __init__(self, config: Config, settings: Settings):
        self.config = config
        self.settings = settings
        self.client = NotionClient(token_v2=config.token_v2)
        self.todo_col = self.client.get_collection_view(
            config.tasks_collection_url, force_refresh=True).collection
        self.scheduled_col = self.client.get_collection_view(
            config.scheduled_collection_url, force_refresh=True).collection


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

    context = Context(config, settings)

    # Add the events
    run_scheduler(context)

    # HACK: remove duplicate tags
    remove_duplicate_tags(context)


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


def get_row_prop(row: CollectionRowBlock, property: str) -> Any:
    try:
        return getattr(row, property)
    except TypeError:
        return None


def create_entries(
    settings: Settings,
    config: Config,
    spec_row: CollectionRowBlock,
) -> Generator[Dict[str, Any], None, None]:
    r = RecurringEvent(now_date=spec_row.start_date.start)
    times = r.parse(spec_row.recurrence)
    rr = rrule.rrulestr(r.get_RFC_rrule(), dtstart=spec_row.start_date.start)

    if get_row_prop(spec_row, 'not_on'):
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
        if get_row_prop(spec_row, 'not_on') and dt.date() in not_dates:
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
        if get_row_prop(spec_row, 'reminder'):
            reminder = parse_reminder(spec_row.reminder)

        if get_row_prop(spec_row, 'include_time'):
            if get_row_prop(spec_row, 'duration'):
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


def remove_duplicate_tags(context: Context) -> None:
    def find_duplicates(tags) -> List[str]:
        rec = defaultdict(list)
        for item in tags:
            rec[item["value"]].append(item["id"])

        dups = []
        for name, ids in rec.items():
            if len(ids) > 1:
                for d_id in ids[1:]:
                    dups.append(d_id)
                    logging.info(f"Found duplicate '{name}' with id '{d_id}'")

        return dups

    def build_ops(dups, path, record_id):
        ops = []
        for d in dups:
            ops.append(
                notion.operations.build_operations(
                    record_id=record_id,
                    command='keyedObjectListRemove',
                    table='collection',
                    path=path,
                    args={'remove': {
                        'id': d
                    }},
                ))

        return ops

    props = context.todo_col.get_schema_properties()

    def run_transaction(property: str, col_id: str):
        tags = next(x for x in props if x["name"].lower() == property)
        dups = find_duplicates(tags["options"])
        ops = build_ops(
            dups,
            f"schema.{tags['id']}.options",
            col_id,
        )
        logging.info(f"Removing duplicates for '{property}'")

        if not context.settings.dry_run:
            context.client.submit_transaction(ops)

    # Tags
    run_transaction(context.config.tags_property, context.todo_col.id)

    # Status
    if context.config.status_property is not None:
        run_transaction(context.config.status_property, context.todo_col.id)


def run_scheduler(context: Context, only_remove=False) -> None:
    def tag_filter(tag: str) -> Dict[str, Any]:
        return {
            'property': context.config.tags_property,
            'filter': {
                'operator': 'enum_contains',
                'value': {
                    'type': 'exact',
                    'value': tag
                },
            }
        }

    scheduled_filter: Dict[str, Any] = {"filters": [], "operator": "or"}
    if not context.settings.append:
        scheduled_filter['filters'].append(
            tag_filter(context.config.scheduled_tag))
    if context.settings.delete_rescheduled:
        scheduled_filter['filters'].append(
            tag_filter(context.config.rescheduled_tag))

    # remove all scheduled
    rows_to_remove = [
        CollectionRowBlock(context.client, row.id)
        for row in context.todo_col.get_rows(filter=scheduled_filter)
    ]
    with context.client.as_atomic_transaction():
        for row in rows_to_remove:
            title = row.title
            if not context.settings.dry_run:
                row.remove()
            logging.info(f"Removed pre-existing scheduled row '{title}'")

    if only_remove:
        return

    rows_to_add = [
        CollectionRowBlock(context.client, row.id)
        for row in context.scheduled_col.get_rows()
    ]
    # add new
    for row in rows_to_add:
        for entry in create_entries(context.settings, context.config, row):
            context.todo_col.add_row(**entry, update_views=False)
