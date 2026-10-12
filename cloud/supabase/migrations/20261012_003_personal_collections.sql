create function public.gandharva_save_playlist(p_id uuid,p_name text,p_tracks uuid[],p_delete boolean default false)
returns uuid language plpgsql security invoker set search_path='' as $$
declare v_id uuid:=p_id; v_owner uuid:=(select auth.uid()); v_count integer;
begin
 if v_owner is null then raise exception 'Authentication required' using errcode='42501'; end if;
 if p_delete then
  delete from public.gandharva_playlists where id=p_id and owner_id=v_owner;
  if not found then raise exception 'Playlist unavailable' using errcode='42501'; end if;
  return p_id;
 end if;
 if p_name is null or char_length(trim(p_name)) not between 1 and 100 or p_tracks is null or cardinality(p_tracks)>200 then raise exception 'Invalid playlist'; end if;
 select count(distinct x) into v_count from unnest(p_tracks) x;
 if v_count<>cardinality(p_tracks) then raise exception 'Duplicate or null tracks'; end if;
 select count(*) into v_count from public.gandharva_tracks where id=any(p_tracks) and publication_state='listed';
 if v_count<>cardinality(p_tracks) then raise exception 'Unavailable tracks' using errcode='42501'; end if;
 if v_id is null then
  insert into public.gandharva_playlists(owner_id,name) values(v_owner,trim(p_name)) returning id into v_id;
 else
  update public.gandharva_playlists set name=trim(p_name) where id=v_id and owner_id=v_owner;
  if not found then raise exception 'Playlist unavailable' using errcode='42501'; end if;
 end if;
 delete from public.gandharva_playlist_items where playlist_id=v_id;
 insert into public.gandharva_playlist_items(playlist_id,track_id,position)
 select v_id,x.id,(x.position-1)::integer from unnest(p_tracks) with ordinality as x(id,position);
 return v_id;
end $$;
revoke all on function public.gandharva_save_playlist(uuid,text,uuid[],boolean) from public,anon;
grant execute on function public.gandharva_save_playlist(uuid,text,uuid[],boolean) to authenticated;

create function public.gandharva_set_favorite(p_track_id uuid,p_liked boolean)
returns void language plpgsql security invoker set search_path='' as $$
declare v_owner uuid:=(select auth.uid());
begin
 if v_owner is null then raise exception 'Authentication required' using errcode='42501'; end if;
 if p_liked is null then raise exception 'Boolean required'; end if;
 if not p_liked then
  delete from public.gandharva_favorites where user_id=v_owner and track_id=p_track_id;
  return;
 end if;
 if not exists(select 1 from public.gandharva_tracks where id=p_track_id and publication_state='listed') then raise exception 'Unavailable track' using errcode='42501'; end if;
 insert into public.gandharva_favorites(user_id,track_id) values(v_owner,p_track_id) on conflict do nothing;
end $$;
revoke all on function public.gandharva_set_favorite(uuid,boolean) from public,anon;
grant execute on function public.gandharva_set_favorite(uuid,boolean) to authenticated;
