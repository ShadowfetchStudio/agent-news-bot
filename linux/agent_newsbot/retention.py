"""Retention boundaries shared by journal readers and explicit maintenance."""
import json

SCHEMA = "CREATE TABLE IF NOT EXISTS journal_retention(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL)"


def retention_state(db):
    row = db.execute('SELECT payload FROM journal_retention WHERE id=1').fetchone()
    state = json.loads(row[0]) if row else {}
    floor = state.get('pruned_through', 0)
    if type(floor) is not int or floor < 0:
        raise ValueError('invalid journal retention boundary')
    return dict(state, pruned_through=floor)


def retained_guard(db, cursor):
    from .consumers import ConsumerConflict
    floor = retention_state(db)['pruned_through']
    if cursor < floor:
        raise ConsumerConflict('history was archived; explicitly create/reset this consumer from retained or now')
    return floor


def public_retention(db):
    state = retention_state(db)
    return {key: state.get(key, default) for key, default in (
        ('pruned_through', 0), ('pruned_events', 0), ('last_pruned_at', None))}
