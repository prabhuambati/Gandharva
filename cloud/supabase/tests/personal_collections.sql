begin;
do $$
declare a uuid:=gen_random_uuid();b uuid:=gen_random_uuid();t1 uuid;t2 uuid;p uuid;n integer;
begin
 select id into t1 from public.gandharva_tracks order by id limit 1;
 select id into t2 from public.gandharva_tracks order by id offset 1 limit 1;
 if t1 is null or t2 is null then raise exception 'Reviewed catalog required'; end if;
 insert into auth.users(id,email) values(a,a::text||'@gandharva-test.invalid'),(b,b::text||'@gandharva-test.invalid');
 perform set_config('request.jwt.claims',json_build_object('sub',a::text,'role','authenticated')::text,true);
 execute 'set local role authenticated';
 p:=public.gandharva_save_playlist(null,'Fixture only',array[t1,t2],false);
 perform public.gandharva_save_playlist(p,'Reordered',array[t2,t1],false);
 select count(*) into n from public.gandharva_playlist_items where playlist_id=p and track_id=t2 and position=0;
 if n<>1 then raise exception 'Playlist ordering failed'; end if;
 perform public.gandharva_set_favorite(t1,true);perform public.gandharva_set_favorite(t1,true);
 select count(*) into n from public.gandharva_favorites where user_id=a and track_id=t1;
 if n<>1 then raise exception 'Favorite retry idempotency failed'; end if;
 perform set_config('request.jwt.claims',json_build_object('sub',b::text,'role','authenticated')::text,true);
 select count(*) into n from public.gandharva_playlists where id=p;
 if n<>0 then raise exception 'Foreign playlist visible'; end if;
 begin
  perform public.gandharva_save_playlist(p,'Foreign overwrite',array[t1],false);
  raise exception 'Foreign playlist overwrite allowed';
 exception when insufficient_privilege then null; end;
 begin
  perform public.gandharva_save_playlist(p,'',array[]::uuid[],true);
  raise exception 'Foreign playlist deletion allowed';
 exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claims',json_build_object('sub',a::text,'role','authenticated')::text,true);
 begin
  perform public.gandharva_save_playlist(p,'Duplicates',array[t1,t1],false);
  raise exception 'Duplicate tracks allowed';
 exception when raise_exception then
  if sqlerrm<>'Duplicate or null tracks' then raise;end if;
 end;
 select count(*) into n from public.gandharva_playlist_items where playlist_id=p;
 if n<>2 then raise exception 'Invalid save damaged original playlist';end if;
 perform public.gandharva_set_favorite(t1,false);perform public.gandharva_save_playlist(p,'',array[]::uuid[],true);
 select count(*) into n from public.gandharva_playlist_items where playlist_id=p;
 if n<>0 then raise exception 'Delete did not cascade';end if;
end $$;
rollback;
select 'passed: atomic ordering, duplicate rejection, idempotent favorite, cross-owner read/write/delete denial; fixtures rolled back' as validation;
