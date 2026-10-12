-- Schema only. Import your own reviewed metadata separately.
alter table public.gandharva_tracks add column creator_credits text[] not null default '{}';
alter table public.gandharva_tracks add column performance_type text not null default '';
alter table public.gandharva_tracks add column instrument text not null default '';
