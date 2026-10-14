"""Regression coverage for source batches and cancellation after a worker crash."""
import hashlib
import json

import pytest

from app.adapters.fake import FakeAdapter
from app.adapters.registry import Registry
from app.auth import create_owner
from app.db import connect, init_db
from app.services import jobs, table_imports
from app.services.approvals import approve_plan
from app.services.execution import execute_plan, run_attempt
from app.services.plans import CostSpec, PlanSpec, create_plan
from app.services.products import create_product
from app.worker import run_worker


@pytest.fixture
def state(tmp_path):
    db = tmp_path / 'app.db'
    init_db(db)
    owner = create_owner(db, 'review', 'test-only-password')
    return db, owner


def imported(db, owner, kind, text, filename='filled.csv'):
    batch = table_imports.prepare(db, owner, filename, text.encode(), kind)
    table = batch['tables'][0]
    result = table_imports.commit(db, owner, batch['id'], table['name'], table['mapping'])
    return batch, result


@pytest.mark.parametrize('kind,header', [('products', '商品标题,品类'), ('market', '关键词,品类组')])
def test_source_batches_ignore_filename_and_accept_changed_content(state, kind, header):
    db, owner = state
    first = header + '\n甲,文具\n乙,文具\n'
    batch, result = imported(db, owner, kind, first)
    assert result['imported'] == 2
    table = batch['tables'][0]
    assert table_imports.commit(db, owner, batch['id'], table['name'], table['mapping']) == result
    assert imported(db, owner, kind, first)[1]['duplicates'] == 2
    assert imported(db, owner, kind, first, 'renamed.csv')[1]['duplicates'] == 2
    assert imported(db, owner, kind, header + '\n丙,文具\n')[1]['imported'] == 1
    # Without a business key, changed content/order is explicitly a new source.
    reordered = header + '\n乙,文具\n甲,文具\n'
    assert imported(db, owner, kind, reordered)[1]['imported'] == 2
    assert imported(db, owner, kind, reordered)[1]['duplicates'] == 2
    assert len(table_imports.records(db, owner, kind)) == 5


@pytest.mark.parametrize('kind,header', [('products', '商品标题,品类'), ('market', '关键词,品类组')])
def test_legacy_filename_keys_still_deduplicate_exact_source(state, kind, header):
    db, owner = state
    text = header + '\n甲,文具\n'
    batch, _ = imported(db, owner, kind, text, 'legacy.csv')
    legacy_key = hashlib.sha256(table_imports.dump({'file': 'legacy.csv', 'sheet': '数据表', 'row': 2}).encode()).hexdigest()
    c = connect(db)
    c.execute('UPDATE imported_rows SET row_hash=? WHERE import_id=?', (legacy_key, batch['id']))
    c.commit()
    c.close()
    assert imported(db, owner, kind, text, 'renamed.csv')[1]['duplicates'] == 1
    assert imported(db, owner, kind, header + '\n乙,文具\n', 'legacy.csv')[1]['imported'] == 1


def prepared(state):
    db, owner = state
    product = create_product(db, title='合成测试商品', category='测试', strategy_mode='PUHUO')
    plan = create_plan(db, spec=PlanSpec(product_id=product['id'], action='fake', provider='fake',
        requires_content=True, requires_cost=True,
        cost=CostSpec(estimated_amount='0.80', maximum_amount='1.00', quote_source='synthetic-fixture',
                      failure_billing='free', retry_needs_new_approval=True), payload={'prompt': '测试'}))
    for kind in ('CONTENT', 'COST'):
        approve_plan(db, plan_id=plan['id'], plan_hash=plan['plan_hash'], owner_id=owner, kind=kind)
    fake = FakeAdapter()
    registry = Registry({'fake': fake})
    attempt = execute_plan(db, plan_id=plan['id'], registry=registry, deferred=True)
    return db, plan, attempt, fake, registry


def expire(db, jid):
    c = connect(db)
    c.execute("UPDATE jobs SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?", (jid,))
    c.commit()
    c.close()
    return jobs.recover_expired(db)


def assert_cancelled(db, plan, attempt, fake):
    assert fake.submit_count == 0
    assert jobs.get_job(db, attempt['job_id'])['state'] == 'CANCELLED'
    c = connect(db)
    assert c.execute('SELECT state FROM external_attempts WHERE id=?', (attempt['id'],)).fetchone()[0] == 'FAILED'
    assert c.execute('SELECT state FROM operation_plans WHERE id=?', (plan['id'],)).fetchone()[0] == 'REVOKED'
    entries = c.execute('SELECT entry_kind,amount_minor FROM cost_ledger WHERE attempt_id=?', (attempt['id'],)).fetchall()
    assert sorted(tuple(row) for row in entries) == [('RELEASED', 100), ('RESERVED', 100)]
    assert c.execute("SELECT count(*) FROM audit_events WHERE event_type='PREPARED_CANCELLED'").fetchone()[0] == 1
    c.close()


def test_cancel_claimed_then_crash_recovery_cleans_all_states(state):
    db, plan, attempt, fake, registry = prepared(state)
    jobs.claim_job(db, job_id=attempt['job_id'])
    jobs.cancel_job(db, attempt['job_id'])
    assert expire(db, attempt['job_id'])[0]['to'] == 'QUEUED'
    run_worker(data_dir=db.parent, once=True, registry=registry)
    assert_cancelled(db, plan, attempt, fake)


def test_cancel_after_claim_uses_live_flag_and_old_lease_cannot_cancel(state):
    db, plan, attempt, fake, registry = prepared(state)
    old = jobs.claim_job(db, job_id=attempt['job_id'])
    expire(db, attempt['job_id'])
    current = jobs.claim_job(db, job_id=attempt['job_id'])
    jobs.cancel_job(db, attempt['job_id'])
    with pytest.raises(ValueError, match='租约'):
        run_attempt(db, old, registry)
    assert jobs.get_job(db, attempt['job_id'])['state'] == 'RUNNING'
    result = run_attempt(db, current, registry)
    assert result['state'] == 'FAILED'
    assert_cancelled(db, plan, attempt, fake)


def test_cancel_after_submission_remains_reconciliation_and_keeps_reservation(state):
    db, plan, attempt, fake, registry = prepared(state)
    jobs.claim_job(db, job_id=attempt['job_id'])
    c = connect(db)
    c.execute("UPDATE external_attempts SET state='SUBMITTED' WHERE id=?", (attempt['id'],))
    c.commit()
    c.close()
    jobs.cancel_job(db, attempt['job_id'])
    assert expire(db, attempt['job_id'])[0]['to'] == 'RECONCILING'
    run_worker(data_dir=db.parent, once=True, registry=registry)
    assert fake.submit_count == 0
    c = connect(db)
    assert c.execute('SELECT state FROM external_attempts WHERE id=?', (attempt['id'],)).fetchone()[0] == 'UNKNOWN'
    assert c.execute('SELECT state FROM operation_plans WHERE id=?', (plan['id'],)).fetchone()[0] == 'UNKNOWN'
    assert c.execute("SELECT count(*) FROM cost_ledger WHERE entry_kind='RELEASED'").fetchone()[0] == 0
    c.close()

@pytest.mark.parametrize('kind,header', [('products', '商品标题,品类'), ('market', '关键词,品类组')])
def test_same_named_template_can_import_distinct_new_batch(state, kind, header):
    db, owner = state
    assert imported(db, owner, kind, header + '\n第一批,文具\n')[1]['imported'] == 1
    assert imported(db, owner, kind, header + '\n第二批,文具\n')[1]['imported'] == 1
