alter table public.alert_events
    add column if not exists email_status text not null default 'pending',
    add column if not exists email_attempts integer not null default 0,
    add column if not exists email_sent_at timestamptz,
    add column if not exists email_last_error text,
    add column if not exists email_claimed_at timestamptz;

alter table public.alert_events drop constraint if exists alert_events_email_status_check;
alter table public.alert_events add constraint alert_events_email_status_check
    check (email_status in ('pending', 'sending', 'sent', 'failed'));

create index if not exists alert_events_pending_email_idx
    on public.alert_events (email_status, created_at)
    where email_status in ('pending', 'sending', 'failed');

create or replace function public.claim_alert_email_deliveries(p_limit integer default 25)
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
        where ae.email_attempts < 5
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
begin
    select * into v_alert from public.price_alerts where id = p_alert_id for update;
    if not found then
        raise exception 'price alert not found';
    end if;
    select ticker into v_ticker from public.saved_plans where id = v_alert.saved_plan_id;

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
    end if;
end;
$$;

create or replace function public.record_alert_email_delivery(
    p_event_id uuid,
    p_delivered boolean,
    p_error text default null
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
    update public.alert_events
    set email_status = case when p_delivered then 'sent' else 'failed' end,
        email_attempts = email_attempts + 1,
        email_sent_at = case when p_delivered then now() else email_sent_at end,
        email_last_error = case when p_delivered then null else left(p_error, 500) end,
        email_claimed_at = case when p_delivered then null else email_claimed_at end
    where id = p_event_id and email_status = 'sending';
end;
$$;

revoke all on function public.claim_alert_email_deliveries(integer) from public, anon, authenticated;
grant execute on function public.claim_alert_email_deliveries(integer) to service_role;
revoke all on function public.record_alert_email_delivery(uuid, boolean, text) from public, anon, authenticated;
grant execute on function public.record_alert_email_delivery(uuid, boolean, text) to service_role;
