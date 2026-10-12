-- Gandharva foundation. No user/catalog/media data is imported.
-- Authentication is owned by Supabase Auth, not a copied SQLite password table.
create table public.gandharva_profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text not null check (char_length(display_name) between 1 and 80),
  created_at timestamptz not null default now()
);
create table public.gandharva_roles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  role text not null check (role in ('listener','creator','admin'))
);
create table public.gandharva_creators (
  id uuid primary key default gen_random_uuid(),
  instagram_handle text not null unique check (instagram_handle ~ '^[A-Za-z0-9_.]{1,30}$'),
  display_name text not null check (char_length(display_name) between 1 and 100),
  profile_url text check (profile_url is null or profile_url ~ '^https://(www\.)?instagram\.com/'),
  published boolean not null default false,
  created_at timestamptz not null default now()
);
create table public.gandharva_tracks (
  id uuid primary key default gen_random_uuid(),
  legacy_id text unique,
  creator_id uuid not null references public.gandharva_creators(id),
  title text not null check (char_length(title) between 1 and 200),
  title_provenance text not null default 'unidentified' check (title_provenance in ('unidentified','caption','creator_supplied')),
  source_url text not null unique check (source_url ~ '^https://(www\.)?instagram\.com/'),
  publication_state text not null default 'draft' check (publication_state in ('draft','listed','withdrawn')),
  created_at timestamptz not null default now()
);
-- No media paths, private evidence or access credentials in the catalog table.
create table public.gandharva_media (
  track_id uuid primary key references public.gandharva_tracks(id) on delete cascade,
  audio_key text unique,
  artwork_key text,
  duration_seconds numeric check (duration_seconds > 0 and duration_seconds <= 86400),
  permission_state text not null default 'unverified' check (permission_state in ('unverified','authorized','own_content','withdrawn')),
  private_permission_reference text,
  verified_at timestamptz,
  check (permission_state not in ('authorized','own_content') or (verified_at is not null and private_permission_reference is not null and char_length(private_permission_reference) > 0))
);
create table public.gandharva_playlists (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (char_length(name) between 1 and 100),
  created_at timestamptz not null default now()
);
create index gandharva_playlists_owner_idx on public.gandharva_playlists(owner_id);
create table public.gandharva_playlist_items (
  playlist_id uuid not null references public.gandharva_playlists(id) on delete cascade,
  track_id uuid not null references public.gandharva_tracks(id) on delete cascade,
  position integer not null check (position >= 0),
  primary key (playlist_id,track_id),
  unique (playlist_id,position) deferrable initially immediate
);
create index gandharva_playlist_items_track_idx on public.gandharva_playlist_items(track_id);
create table public.gandharva_favorites (
  user_id uuid not null references auth.users(id) on delete cascade,
  track_id uuid not null references public.gandharva_tracks(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id,track_id)
);
create index gandharva_favorites_track_idx on public.gandharva_favorites(track_id);
create table public.gandharva_analytics_preferences (
  user_id uuid primary key references auth.users(id) on delete cascade,
  listening boolean not null default false,
  charts boolean not null default false
);
-- Client cannot insert qualifying events or increment anonymous counts.
create table public.gandharva_listening_events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  track_id uuid not null references public.gandharva_tracks(id),
  qualified boolean not null default false,
  contributes boolean not null default false,
  listened_seconds numeric not null default 0 check (listened_seconds >= 0),
  created_at timestamptz not null default now(),
  expires_at timestamptz not null default (now() + interval '90 days')
);
create index gandharva_events_owner_idx on public.gandharva_listening_events(user_id,created_at);
create index gandharva_events_track_idx on public.gandharva_listening_events(track_id);
create index gandharva_events_expiry_idx on public.gandharva_listening_events(expires_at);
create table public.gandharva_daily_totals (
  utc_day date not null,
  track_id uuid not null references public.gandharva_tracks(id),
  plays bigint not null default 0 check (plays >= 0),
  primary key (utc_day,track_id)
);
create index gandharva_daily_track_idx on public.gandharva_daily_totals(track_id);
create table public.gandharva_creator_bindings (
  creator_id uuid primary key references public.gandharva_creators(id),
  user_id uuid not null references auth.users(id) on delete cascade,
  verified_at timestamptz not null,
  private_evidence_reference text not null
);
create index gandharva_bindings_user_idx on public.gandharva_creator_bindings(user_id);

-- Opt-in access grants. No anonymous table access. Every table has RLS.
alter table public.gandharva_profiles enable row level security;
alter table public.gandharva_roles enable row level security;
alter table public.gandharva_creators enable row level security;
alter table public.gandharva_tracks enable row level security;
alter table public.gandharva_media enable row level security;
alter table public.gandharva_playlists enable row level security;
alter table public.gandharva_playlist_items enable row level security;
alter table public.gandharva_favorites enable row level security;
alter table public.gandharva_analytics_preferences enable row level security;
alter table public.gandharva_listening_events enable row level security;
alter table public.gandharva_daily_totals enable row level security;
alter table public.gandharva_creator_bindings enable row level security;

revoke all on public.gandharva_profiles, public.gandharva_roles, public.gandharva_creators,
 public.gandharva_tracks, public.gandharva_media, public.gandharva_playlists,
 public.gandharva_playlist_items, public.gandharva_favorites,
 public.gandharva_analytics_preferences, public.gandharva_listening_events,
 public.gandharva_daily_totals, public.gandharva_creator_bindings from anon, authenticated;
grant select,insert,update on public.gandharva_profiles to authenticated;
grant select on public.gandharva_roles,public.gandharva_creators,public.gandharva_tracks to authenticated;
grant select,insert,update,delete on public.gandharva_playlists,public.gandharva_playlist_items,public.gandharva_favorites to authenticated;
grant select,insert,update on public.gandharva_analytics_preferences to authenticated;
grant select on public.gandharva_listening_events to authenticated;
-- Backend credential only; never embed this role's secret in frontend code.
grant all on public.gandharva_profiles,public.gandharva_roles,public.gandharva_creators,
 public.gandharva_tracks,public.gandharva_media,public.gandharva_playlists,
 public.gandharva_playlist_items,public.gandharva_favorites,
 public.gandharva_analytics_preferences,public.gandharva_listening_events,
 public.gandharva_daily_totals,public.gandharva_creator_bindings to service_role;

create policy profile_read_self on public.gandharva_profiles for select to authenticated using (id=(select auth.uid()));
create policy profile_insert_self on public.gandharva_profiles for insert to authenticated with check (id=(select auth.uid()));
create policy profile_update_self on public.gandharva_profiles for update to authenticated using (id=(select auth.uid())) with check (id=(select auth.uid()));
create policy role_read_self on public.gandharva_roles for select to authenticated using (user_id=(select auth.uid()));
create policy creator_read_listed on public.gandharva_creators for select to authenticated using (published);
create policy track_read_listed on public.gandharva_tracks for select to authenticated using (publication_state='listed' and exists(select 1 from public.gandharva_creators c where c.id=creator_id and c.published));
create policy playlists_owner on public.gandharva_playlists for all to authenticated using (owner_id=(select auth.uid())) with check (owner_id=(select auth.uid()));
create policy playlist_items_owner on public.gandharva_playlist_items for all to authenticated
 using (exists(select 1 from public.gandharva_playlists p where p.id=playlist_id and p.owner_id=(select auth.uid())))
 with check (exists(select 1 from public.gandharva_playlists p where p.id=playlist_id and p.owner_id=(select auth.uid())) and exists(select 1 from public.gandharva_tracks t where t.id=track_id and t.publication_state='listed'));
create policy favorites_owner on public.gandharva_favorites for all to authenticated
 using (user_id=(select auth.uid()))
 with check (user_id=(select auth.uid()) and exists(select 1 from public.gandharva_tracks t where t.id=track_id and t.publication_state='listed'));
create policy preferences_owner on public.gandharva_analytics_preferences for all to authenticated using (user_id=(select auth.uid())) with check (user_id=(select auth.uid()));
create policy history_owner on public.gandharva_listening_events for select to authenticated using (user_id=(select auth.uid()) and expires_at>now());

-- Empty private buckets. No client upload/download policies yet, hence deny by default.
insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types) values
 ('gandharva-audio','gandharva-audio',false,10485760,array['audio/mpeg']),
 ('gandharva-artwork','gandharva-artwork',false,5242880,array['image/jpeg','image/png','image/webp']);
