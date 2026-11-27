const lockKey = "62062001";

export async function claimWorker(db) {
  const { rows } = await db.query("select pg_try_advisory_lock($1::bigint) as acquired", [lockKey]);
  return rows[0].acquired;
}

export async function releaseWorker(db) {
  await db.query("select pg_advisory_unlock($1::bigint)", [lockKey]);
}

export async function startAttempt(db, leaseMs) {
  const { rows } = await db.query(`
    update calls_delivery.render_slot
    set token = token + 1, expires_at = clock_timestamp() + $1::double precision * interval '1 millisecond'
    where expires_at <= clock_timestamp()
    returning token, (select valid_until::text from calls_delivery.next_deadline) as valid_until
  `, [leaseMs]);
  return rows[0];
}

export async function extendAttempt(db, token, leaseMs) {
  const { rowCount } = await db.query(`
    update calls_delivery.render_slot
    set expires_at = clock_timestamp() + $2::double precision * interval '1 millisecond'
    where token = $1 and expires_at > clock_timestamp()
  `, [token, leaseMs]);
  if (!rowCount) throw Error("render ownership expired");
}

export async function finishAttempt(db, token) {
  await db.query("update calls_delivery.render_slot set expires_at = '-infinity' where token = $1", [token]);
}

export async function pendingScopes(db, buildId) {
  const { rows } = await db.query(`
    select r.scope, r.revision, shared.revision as shared_revision, list.revision as list_revision
    from calls_delivery.revision r
    cross join calls_delivery.revision shared
    cross join calls_delivery.revision list
    left join calls_delivery.manifest m on m.build_id = $1 and m.scope = r.scope
    where r.scope <> '@shared' and shared.scope = '@shared' and list.scope = '@earnings'
      and (m.revision is distinct from r.revision or m.shared_revision is distinct from shared.revision
           or m.valid_until <= statement_timestamp()
           or (m.discovery_revision is not null and m.discovery_revision <> list.revision))
    order by r.changed_at, r.scope
  `, [buildId]);
  return rows;
}

export async function publish(db, buildId, task, token, result) {
  await db.query("begin");
  try {
    // Lock source revisions in the same order as the invalidation trigger. A
    // write commits either before this comparison or after the manifest swap.
    const expected = new Map([[task.scope, task.revision], ["@shared", task.shared_revision]]);
    if (result.discovery) expected.set("@earnings", task.list_revision);
    const { rows } = await db.query(`
      select scope, revision from calls_delivery.revision
      where scope = any($1) order by scope for share
    `, [[...expected.keys()]]);
    const slot = await db.query(`
      select token from calls_delivery.render_slot
      where token = $1 and expires_at > clock_timestamp() and $2::timestamptz > clock_timestamp() for update
    `, [token, result.validUntil]);
    if (slot.rowCount !== 1 || rows.length !== expected.size || rows.some((r) => expected.get(r.scope) !== r.revision)) {
      await db.query("rollback");
      return false;
    }

    await db.query("delete from calls_delivery.manifest where build_id = $1 and scope = $2", [buildId, task.scope]);
    await db.query(`
      insert into calls_delivery.manifest
        (build_id, scope, revision, shared_revision, discovery_revision, company_exists, render_token, valid_until)
      values ($1, $2, $3, $4, $5, $6, $7, $8)
    `, [buildId, task.scope, task.revision, task.shared_revision, result.discovery ? task.list_revision : null, result.exists, token, result.validUntil]);
    await db.query(`
      insert into calls_delivery.response (build_id, scope, path, variant, selected_call, digest, body)
      select $1, $2, input.path, input.variant, input.selected_call, input.digest, input.body
      from unnest($3::text[], $4::text[], $5::text[], $6::text[], $7::bytea[])
        as input(path, variant, selected_call, digest, body)
    `, [
      buildId, task.scope,
      result.parts.map((p) => p.path),
      result.parts.map((p) => p.variant),
      result.parts.map((p) => p.selectedCall),
      result.parts.map((p) => p.digest),
      result.parts.map((p) => p.body),
    ]);
    await db.query("commit");
    return true;
  } catch (error) {
    await db.query("rollback");
    throw error;
  }
}
