alter table public.price_alerts drop constraint if exists price_alerts_event_type_check;
alter table public.price_alerts add constraint price_alerts_event_type_check
    check (event_type in (
        'entry_zone', 'confirmation', 'stop', 'target_1', 'target_2',
        'option_entry', 'option_stop', 'option_target_1', 'option_target_2'
    ));

create table if not exists public.option_positions (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.users(id) on delete cascade,
    saved_plan_id uuid not null references public.saved_plans(id) on delete cascade,
    plan_version integer not null,
    underlying_ticker text not null,
    contract_symbol text not null,
    option_type text check (option_type in ('call', 'put')),
    expiry date,
    strike numeric(20, 6),
    lifecycle_state text not null default 'watching_entry'
        check (lifecycle_state in ('watching_entry', 'entry_alerted', 'open', 'closed', 'stopped', 'expired')),
    planned_entry numeric(20, 6) not null check (planned_entry > 0),
    confirmed_entry numeric(20, 6) check (confirmed_entry > 0),
    quantity integer check (quantity > 0),
    entry_alerted_at timestamptz,
    entered_at timestamptz,
    closed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (saved_plan_id, plan_version, contract_symbol)
);

create index if not exists option_positions_user_state_idx
    on public.option_positions (user_id, lifecycle_state, updated_at desc);

alter table public.option_positions enable row level security;

create or replace function public.replace_plan_alert_rules(
    p_user_id uuid,
    p_saved_plan_id uuid,
    p_plan_version integer,
    p_rules jsonb
)
returns setof public.price_alerts
language plpgsql
security definer
set search_path = public
as $$
declare
    v_ticker text;
    v_option_entry jsonb;
    v_position_state text;
begin
    select p.ticker into v_ticker
    from public.saved_plans p
    where p.id = p_saved_plan_id
      and p.user_id = p_user_id
      and p.active_version = p_plan_version;
    if not found then
        raise exception 'saved plan not found or version is not active';
    end if;
    perform 1 from public.price_alerts pa
    where pa.saved_plan_id = p_saved_plan_id
    order by pa.id for update;
    select op.lifecycle_state into v_position_state
    from public.option_positions op
    where op.user_id = p_user_id and op.saved_plan_id = p_saved_plan_id
      and op.plan_version = p_plan_version
    for update;
    if v_position_state in ('entry_alerted', 'open') then
        raise exception 'option position must be resolved before replacing alert rules';
    end if;

    delete from public.price_alerts pa where pa.saved_plan_id = p_saved_plan_id;
    delete from public.option_positions op
    where op.user_id = p_user_id
      and op.saved_plan_id = p_saved_plan_id
      and op.plan_version = p_plan_version;
    insert into public.price_alerts (
        user_id, saved_plan_id, plan_version, event_type, rule_data,
        monitoring_enabled, armed
    )
    select p_user_id, p_saved_plan_id, p_plan_version,
        item->>'event_type', item->'rule_data',
        case when item->>'event_type' in ('option_stop', 'option_target_1', 'option_target_2')
            then false else true end,
        true
    from jsonb_array_elements(p_rules) item;

    select item->'rule_data' into v_option_entry
    from jsonb_array_elements(p_rules) item
    where item->>'event_type' = 'option_entry'
    limit 1;

    if v_option_entry is not null then
        insert into public.option_positions (
            user_id, saved_plan_id, plan_version, underlying_ticker,
            contract_symbol, option_type, expiry, strike, planned_entry,
            lifecycle_state, confirmed_entry, quantity, entered_at, closed_at
        ) values (
            p_user_id, p_saved_plan_id, p_plan_version, v_ticker,
            v_option_entry->>'monitor_symbol', v_option_entry->>'option_type',
            nullif(v_option_entry->>'expiry', '')::date,
            nullif(v_option_entry->>'strike', '')::numeric,
            (v_option_entry->>'price')::numeric,
            'watching_entry', null, null, null, null
        );
    end if;

    return query
    select pa.* from public.price_alerts pa
    where pa.saved_plan_id = p_saved_plan_id order by pa.event_type;
end;
$$;

create or replace function public.save_saved_plan_version(
    p_user_id uuid,
    p_ticker text,
    p_plan_data jsonb,
    p_analysis_timestamp timestamptz
)
returns table (
    plan_id uuid,
    ticker text,
    version integer,
    plan_data jsonb,
    analysis_timestamp timestamptz,
    created_at timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_plan_id uuid;
    v_version integer;
    v_position_state text;
begin
    perform pg_advisory_xact_lock(hashtextextended(p_user_id::text || ':' || upper(trim(p_ticker)), 0));

    select p.id into v_plan_id
    from public.saved_plans p
    where p.user_id = p_user_id and p.ticker = upper(trim(p_ticker));
    if v_plan_id is not null then
        perform 1 from public.price_alerts pa
        where pa.saved_plan_id = v_plan_id
        order by pa.id for update;
        select op.lifecycle_state into v_position_state
        from public.option_positions op
        where op.user_id = p_user_id and op.saved_plan_id = v_plan_id
          and op.lifecycle_state in ('entry_alerted', 'open')
        for update;
    end if;
    if v_position_state in ('entry_alerted', 'open') then
        raise exception 'option position must be resolved before replacing saved plan';
    end if;

    insert into public.saved_plans (user_id, ticker)
    values (p_user_id, upper(trim(p_ticker)))
    on conflict on constraint saved_plans_user_id_ticker_key do nothing;

    select p.id, p.active_version into v_plan_id, v_version
    from public.saved_plans p
    where p.user_id = p_user_id and p.ticker = upper(trim(p_ticker))
    for update;

    if exists (select 1 from public.saved_plan_versions pv where pv.saved_plan_id = v_plan_id) then
        v_version := v_version + 1;
    else
        v_version := 1;
    end if;

    insert into public.saved_plan_versions (saved_plan_id, version, plan_data, analysis_timestamp)
    values (v_plan_id, v_version, p_plan_data, p_analysis_timestamp);
    update public.saved_plans p set active_version = v_version, updated_at = now() where p.id = v_plan_id;
    delete from public.price_alerts pa where pa.saved_plan_id = v_plan_id;

    return query
    select v_plan_id, upper(trim(p_ticker)), pv.version, pv.plan_data, pv.analysis_timestamp, pv.created_at
    from public.saved_plan_versions pv
    where pv.saved_plan_id = v_plan_id and pv.version = v_version;
end;
$$;

create or replace function public.confirm_option_position_entry(
    p_user_id uuid,
    p_saved_plan_id uuid,
    p_plan_version integer,
    p_entry_price numeric,
    p_quantity integer
)
returns public.option_positions
language plpgsql
security definer
set search_path = public
as $$
declare
    v_position public.option_positions%rowtype;
begin
    if p_entry_price <= 0 or p_quantity <= 0 then
        raise exception 'entry price and quantity must be positive';
    end if;

    update public.option_positions op
    set lifecycle_state = 'open', confirmed_entry = p_entry_price,
        quantity = p_quantity, entered_at = now(), updated_at = now()
    where op.user_id = p_user_id
      and op.saved_plan_id = p_saved_plan_id
      and op.plan_version = p_plan_version
      and op.lifecycle_state = 'entry_alerted'
    returning * into v_position;
    if not found then
        raise exception 'option position not found or cannot be opened';
    end if;

    update public.price_alerts pa
    set monitoring_enabled = true, armed = true, updated_at = now()
    where pa.user_id = p_user_id
      and pa.saved_plan_id = p_saved_plan_id
      and pa.plan_version = p_plan_version
      and pa.event_type in ('option_stop', 'option_target_1', 'option_target_2');

    update public.price_alerts pa
    set monitoring_enabled = false, updated_at = now()
    where pa.user_id = p_user_id
      and pa.saved_plan_id = p_saved_plan_id
      and pa.plan_version = p_plan_version
      and pa.event_type = 'option_entry';

    return v_position;
end;
$$;

create or replace function public.record_alert_evaluation(
    p_alert_id uuid,
    p_price numeric,
    p_quote_time timestamptz,
    p_armed boolean,
    p_triggered boolean,
    p_idempotency_key text,
    p_event_data jsonb
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    v_alert public.price_alerts%rowtype;
    v_ticker text;
    v_position_state text;
begin
    select * into v_alert from public.price_alerts where id = p_alert_id for update;
    if not found then
        raise exception 'price alert not found';
    end if;
    select ticker into v_ticker from public.saved_plans where id = v_alert.saved_plan_id;

    if v_alert.event_type like 'option_%' then
        select op.lifecycle_state into v_position_state
        from public.option_positions op
        where op.user_id = v_alert.user_id
          and op.saved_plan_id = v_alert.saved_plan_id
          and op.plan_version = v_alert.plan_version
        for update;
    end if;

    if p_triggered and v_alert.event_type = 'option_entry' and (
        not v_alert.monitoring_enabled or not v_alert.armed
        or v_position_state is distinct from 'watching_entry'
    ) then
        return;
    end if;
    if p_triggered and v_alert.event_type in ('option_stop', 'option_target_1', 'option_target_2')
       and (not v_alert.monitoring_enabled or not v_alert.armed
            or v_position_state is distinct from 'open') then
        return;
    end if;

    update public.price_alerts
    set armed = p_armed, last_price = p_price, last_quote_time = p_quote_time,
        last_triggered_at = case when p_triggered then now() else last_triggered_at end,
        updated_at = now()
    where id = p_alert_id;

    if p_triggered then
        insert into public.alert_events (
            user_id, price_alert_id, saved_plan_id, plan_version, ticker, event_type,
            price, quote_time, event_data, idempotency_key
        ) values (
            v_alert.user_id, v_alert.id, v_alert.saved_plan_id, v_alert.plan_version,
            v_ticker, v_alert.event_type, p_price, p_quote_time, p_event_data, p_idempotency_key
        ) on conflict (idempotency_key) do nothing;

        if v_alert.event_type = 'option_entry' then
            update public.option_positions op
            set lifecycle_state = 'entry_alerted', entry_alerted_at = now(), updated_at = now()
            where op.user_id = v_alert.user_id
              and op.saved_plan_id = v_alert.saved_plan_id
              and op.plan_version = v_alert.plan_version
              and op.lifecycle_state = 'watching_entry';

            update public.price_alerts pa
            set monitoring_enabled = false, armed = false, updated_at = now()
            where pa.id = v_alert.id;
        elsif v_alert.event_type = 'option_stop' then
            update public.option_positions op
            set lifecycle_state = 'stopped', closed_at = now(), updated_at = now()
            where op.user_id = v_alert.user_id and op.saved_plan_id = v_alert.saved_plan_id
              and op.plan_version = v_alert.plan_version and op.lifecycle_state = 'open';
            update public.price_alerts pa set monitoring_enabled = false, armed = false, updated_at = now()
            where pa.user_id = v_alert.user_id and pa.saved_plan_id = v_alert.saved_plan_id
              and pa.plan_version = v_alert.plan_version and pa.event_type like 'option_%';
        elsif v_alert.event_type = 'option_target_1' then
            update public.price_alerts pa set monitoring_enabled = false, armed = false, updated_at = now()
            where pa.id = v_alert.id;
        elsif v_alert.event_type = 'option_target_2' then
            update public.option_positions op
            set lifecycle_state = 'closed', closed_at = now(), updated_at = now()
            where op.user_id = v_alert.user_id and op.saved_plan_id = v_alert.saved_plan_id
              and op.plan_version = v_alert.plan_version and op.lifecycle_state = 'open';
            update public.price_alerts pa set monitoring_enabled = false, armed = false, updated_at = now()
            where pa.user_id = v_alert.user_id and pa.saved_plan_id = v_alert.saved_plan_id
              and pa.plan_version = v_alert.plan_version and pa.event_type like 'option_%';
        end if;
    end if;
end;
$$;

drop function if exists public.claim_alert_email_deliveries(integer);
create or replace function public.claim_alert_email_deliveries(
    p_user_id uuid,
    p_limit integer default 25
)
returns setof public.alert_events
language plpgsql
security definer
set search_path = public
as $$
begin
    return query
    with claimable as (
        select ae.id
        from public.alert_events ae
        where ae.user_id = p_user_id
          and ae.email_attempts < 5
          and (
              ae.email_status = 'pending'
              or (ae.email_status = 'sending' and ae.email_claimed_at < now() - interval '10 minutes')
              or (
                  ae.email_status = 'failed'
                  and ae.email_claimed_at < now() - make_interval(mins => power(2, ae.email_attempts)::integer)
              )
          )
        order by ae.created_at asc
        for update skip locked
        limit greatest(1, least(coalesce(p_limit, 25), 100))
    )
    update public.alert_events ae
    set email_status = 'sending', email_claimed_at = now()
    from claimable
    where ae.id = claimable.id
    returning ae.*;
end;
$$;

revoke all on table public.option_positions from public, anon, authenticated;
revoke all on function public.save_saved_plan_version(uuid, text, jsonb, timestamptz) from public, anon, authenticated;
grant execute on function public.save_saved_plan_version(uuid, text, jsonb, timestamptz) to service_role;
revoke all on function public.confirm_option_position_entry(uuid, uuid, integer, numeric, integer) from public, anon, authenticated;
grant execute on function public.confirm_option_position_entry(uuid, uuid, integer, numeric, integer) to service_role;
revoke all on function public.replace_plan_alert_rules(uuid, uuid, integer, jsonb) from public, anon, authenticated;
grant execute on function public.replace_plan_alert_rules(uuid, uuid, integer, jsonb) to service_role;
revoke all on function public.record_alert_evaluation(uuid, numeric, timestamptz, boolean, boolean, text, jsonb) from public, anon, authenticated;
grant execute on function public.record_alert_evaluation(uuid, numeric, timestamptz, boolean, boolean, text, jsonb) to service_role;
revoke all on function public.claim_alert_email_deliveries(uuid, integer) from public, anon, authenticated;
grant execute on function public.claim_alert_email_deliveries(uuid, integer) to service_role;
