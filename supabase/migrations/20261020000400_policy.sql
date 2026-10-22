-- 0004: module 1 — Fed vs RBI policy tracker
-- A meeting is a core.event (event_type 'policy_meeting', entity = the Fed or RBI) plus this row.
create table policy.meeting (
  event_id         bigint primary key references core.event(id),
  meeting_no       integer unique,          -- RBI MPC numbering (14–62 since 2019); null for the FOMC
  kind             text not null check (kind in ('scheduled','unscheduled','special')),
  is_rate_decision boolean not null,        -- false for RBI's special meeting no. 39 (3 Nov 2022)
  -- the release time (Fed 2:00 pm ET, RBI 10:00 am IST) is core.event.occurs_at
  has_press_conference boolean,
  minutes_due_on   date                     -- RBI: day 14 (RBI Act s.45ZL); FOMC: three weeks
);

-- Every rate in force after each decision, one row per instrument, parsed from the decision document
-- with the span it came from. Unchanged rates get a row too, so each meeting's corridor is complete.
create table policy.rate_setting (
  meeting_event_id bigint not null references policy.meeting(event_id),
  instrument  text not null check (instrument in
                ('fed_target_lower','fed_target_upper',
                 'repo','reverse_repo','sdf','msf','bank_rate','crr')),
  value_pct   numeric(6,3) not null,
  change_bps  smallint not null,           -- vs the previous setting (first 2019 meeting: vs FRED / Dec 2018)
  document_id bigint not null references core.document(id),
  char_start  integer not null,
  char_end    integer not null,
  crosscheck  text,                        -- e.g. 'matches FRED DFEDTARU'
  primary key (meeting_event_id, instrument),
  check (char_end > char_start)
);

create table policy.member (
  id    integer generated always as identity primary key,
  bank  text not null check (bank in ('FED','RBI')),
  name  text not null,
  slug  text not null unique check (slug ~ '^[a-z0-9-]+$'),
  unique (bank, name)
);

-- Curated roster: role, affiliation and FOMC voting status over time.
create table policy.member_term (
  member_id   integer not null references policy.member(id),
  role        text not null check (role in ('fed_chair','fed_vice_chair','fed_governor','fed_bank_president',
                                            'rbi_governor','rbi_deputy_governor','rbi_executive_director',
                                            'mpc_external_member')),
  affiliation text,                        -- 'FRB New York'
  is_voter    boolean not null,
  valid       daterange not null,
  source_note text not null,
  exclude using gist (member_id with =, valid with &&)
);

create table policy.vote (
  meeting_event_id bigint not null references policy.meeting(event_id),
  member_id        integer not null references policy.member(id),
  voted_change_bps smallint,               -- the policy-rate change this member voted for
  stance_vote      text,                   -- RBI members also vote on the stance
  is_dissent       boolean not null,
  dissent_text     text,                   -- 'preferred to lower the target range by 1/4 percentage point'
  document_id      bigint not null references core.document(id),
  char_start       integer not null,
  char_end         integer not null,
  primary key (meeting_event_id, member_id),
  check (char_end > char_start)
);

-- Daily close-to-close reaction, using the first observation after the release (fixing time and zone
-- come from core.series). Both observations are foreign keys, so every reaction traces to its inputs.
create table policy.market_reaction (
  meeting_event_id    bigint not null references policy.meeting(event_id),
  series_id           integer not null references core.series(id),
  pre_obs_date        date not null,
  post_obs_date       date not null,
  pre_value           numeric not null,
  post_value          numeric not null,
  change              numeric not null,
  change_unit         text not null check (change_unit in ('bps','pct')),
  is_surprise_measure boolean not null default false,   -- the 1-day DGS2 change for Fed meetings
  primary key (meeting_event_id, series_id),
  foreign key (series_id, pre_obs_date)  references core.observation (series_id, obs_date),
  foreign key (series_id, post_obs_date) references core.observation (series_id, obs_date),
  check (post_obs_date > pre_obs_date)
);
