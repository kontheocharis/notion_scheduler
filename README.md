# `notion_scheduler`

This tool allows the creation of---*drumroll*---**recurring tasks in Notion!**

This is done by defining a Notion database (which I will call the Scheduled database) that contains rules for recurring tasks.
This tool reads these definitions and creates each occurrence of those tasks in your actual tasks database (which I will call the Tasks database).

**DISCLAIMER**: I am not responsible if this tool causes any data loss in your Notion database. Always back up your data!

# Installation

```
pip3 install notion-scheduler
notion_scheduler -h
```

You can also run this from source by installing all the requirements (see `requirements.txt`) and running `./notion_scheduler.py -h`.

# Fields required in the Scheduled database

The fields below are case insensitive.

| Name of field | Type of field | Description |
|-|-|-|
| Title | Text | The title of the task |
| Recurrence | Text | The recurrence rule for the task (see [recurrent](https://github.com/kvh/recurrent) for supported rules) |
| Not on | Text | A [recurrent](https://github.com/kvh/recurrent) rule that describes dates to exclude |
| Start date | Date | The date from which `notion_scheduler` should start creating this task |
| Include time | Checkbox | Whether to include time in the created tasks (defined in the Recurrence field) |
| Duration | Text | If "Include time", how long should each created task last (see [durations](https://github.com/oleiade/durations) for syntax)? Empty means no end time. |
| Reminder | Text | A [durations](https://github.com/oleiade/durations) rule that describes when you should be reminded. Empty means no reminder. |
| Date field | Text or Select | The name of the date field of the Tasks database |

Any additional fields defined in the Scheduled database will be copied over to each created task, as defined in the `properties_to_sync` configuration option (see below).

# Configuration

The configuration file is located at `$XDG_CONFIG_HOME/notion_scheduler/config.yml`.

## Options

The options below should be in YAML format in the configuration file.

| Name of option | Type | Description |
|-|-|-|
| `todo_collection_url` | `str` | The url of the Tasks database. |
| `scheduled_collection_url` | `str` | The url of the Scheduled database. |
| `token_v2` | `str` | Notion v2 token. Get by inspecting your browser's cookies on a Notion page. |
| `properties_to_sync` | `List[str]` | Additional properties to sync over from the Scheduled database. |
| `tags_property` | `str` | The Select field named by this option for which to add the tag `scheduled_tag` for each created task. |
| `scheduled_tag` | `str` | The tag to put in `tags_property` for each created task. Allows `notion_scheduler` to keep track of which tasks in the Tasks database it has generated, so that it can replace them with the updated ones on every run. |
| `rescheduled_tag` | `str` | If you ever manually reschedule a scheduled task in the Tasks database, remove the tag `scheduled_tag` from the field `tags_property`, and add the tag defined by this option. This allows control over which tasks `notion_scheduler` deletes on re-run (see command-line options). |
| `status_property` | `Optional[str]` | If this exists, set the Select field named by this option to `status_before_today` if a created task is scheduled for before today, or `status_after_today` if a created task is scheduled for on or after today. |
| `status_before_today` | `str` | See `status_property` |
| `status_after_today` | `str` | See `status_property` |

