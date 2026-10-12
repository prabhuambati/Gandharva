-- Additive index recommended by Supabase's foreign-key advisor.
create index gandharva_tracks_creator_idx on public.gandharva_tracks(creator_id);
