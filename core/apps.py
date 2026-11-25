from django.apps import AppConfig
from django.db.backends.signals import connection_created


def _configure_sqlite_pragmas(sender, connection, **kwargs):
    """Configures production SQLite PRAGMAs on every new connection."""
    if connection.vendor != 'sqlite':
        return
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA journal_mode=WAL;')
        cursor.execute('PRAGMA synchronous=NORMAL;')
        cursor.execute('PRAGMA busy_timeout=20000;')
        cursor.execute('PRAGMA cache_size=-64000;')
        cursor.execute('PRAGMA temp_store=MEMORY;')
        cursor.execute('PRAGMA foreign_keys=ON;')


def _on_data_changed(sender, **kwargs):
    from .cache_utils import invalidate_on_commit
    invalidate_on_commit()


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        from django.db.models.signals import m2m_changed, post_delete, post_save
        from .models import (
            Division,
            Exam,
            ExamTimetable,
            MasterSubject,
            PortionSubmission,
            QuestionPatternItem,
            TimetableEntry,
        )

        connection_created.connect(_configure_sqlite_pragmas, dispatch_uid='core_sqlite_pragmas')

        for model in (
            MasterSubject,
            Division,
            Exam,
            PortionSubmission,
            QuestionPatternItem,
            ExamTimetable,
            TimetableEntry,
        ):
            post_save.connect(
                _on_data_changed,
                sender=model,
                dispatch_uid=f'core_cache_save_{model.__name__}',
            )
            post_delete.connect(
                _on_data_changed,
                sender=model,
                dispatch_uid=f'core_cache_del_{model.__name__}',
            )

        m2m_changed.connect(
            _on_data_changed,
            sender=ExamTimetable.divisions.through,
            dispatch_uid='core_cache_m2m_tt_div',
        )
