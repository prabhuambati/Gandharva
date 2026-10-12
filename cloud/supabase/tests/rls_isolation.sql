-- Fixtures are uncommitted, never emailed, and rolled back. No production users are imported.
begin;
do $$
declare
  a uuid := gen_random_uuid(); b uuid := gen_random_uuid();
  c uuid := gen_random_uuid(); t uuid := gen_random_uuid();
  pa uuid := gen_random_uuid(); pb uuid := gen_random_uuid(); n integer;
begin
  insert into auth.users(id,email) values (a,a::text||'@gandharva-test.invalid'),(b,b::text||'@gandharva-test.invalid');
  insert into public.gandharva_profiles(id,display_name) values (a,'Fixture A'),(b,'Fixture B');
  insert into public.gandharva_roles(user_id,role) values (a,'listener'),(b,'listener');
  insert into public.gandharva_creators(id,instagram_handle,display_name,published) values (c,'gandharva_test','Fixture creator',true);
  insert into public.gandharva_tracks(id,creator_id,title,source_url,publication_state) values (t,c,'Fixture track','https://www.instagram.com/reel/FIXTURE_ONLY/','listed');
  insert into public.gandharva_playlists(id,owner_id,name) values (pa,a,'A private'),(pb,b,'B private');
  insert into public.gandharva_listening_events(user_id,track_id) values(a,t),(b,t);
  perform set_config('request.jwt.claims',json_build_object('sub',a::text,'role','authenticated')::text,true);
  execute 'set local role authenticated';

  select count(*) into n from public.gandharva_profiles;
  if n<>1 then raise exception 'Profile isolation failed'; end if;
  select count(*) into n from public.gandharva_playlists;
  if n<>1 then raise exception 'Playlist isolation failed'; end if;
  select count(*) into n from public.gandharva_listening_events;
  if n<>1 then raise exception 'History isolation failed'; end if;
  update public.gandharva_playlists set name='Unauthorized' where id=pb;
  get diagnostics n=row_count;
  if n<>0 then raise exception 'Cross-owner playlist update allowed'; end if;
  begin
    update public.gandharva_playlists set owner_id=b where id=pa;
    raise exception 'Playlist ownership transfer allowed';
  exception when insufficient_privilege then null; end;
  begin
    insert into public.gandharva_favorites(user_id,track_id) values(b,t);
    raise exception 'Cross-owner favorite insert allowed';
  exception when insufficient_privilege then null; end;
  insert into public.gandharva_favorites(user_id,track_id) values(a,t);
  insert into public.gandharva_playlist_items(playlist_id,track_id,position) values(pa,t,0);
  begin
    insert into public.gandharva_playlist_items(playlist_id,track_id,position) values(pb,t,0);
    raise exception 'Cross-owner playlist item allowed';
  exception when insufficient_privilege then null; end;
  begin
    update public.gandharva_roles set role='admin' where user_id=a;
    raise exception 'Self admin escalation allowed';
  exception when insufficient_privilege then null; end;
  begin
    insert into public.gandharva_daily_totals(utc_day,track_id,plays) values(current_date,t,1000);
    raise exception 'Client chart inflation allowed';
  exception when insufficient_privilege then null; end;
  begin
    select count(*) into n from public.gandharva_media;
    raise exception 'Private media table access allowed';
  exception when insufficient_privilege then null; end;
  begin
    select count(*) into n from public.gandharva_creator_bindings;
    raise exception 'Private creator evidence access allowed';
  exception when insufficient_privilege then null; end;
  select count(*) into n from storage.objects where bucket_id in ('gandharva-audio','gandharva-artwork');
  if n<>0 then raise exception 'Unexpected client storage access'; end if;
  execute 'set local role anon';
  begin
    select count(*) into n from public.gandharva_tracks;
    raise exception 'Anonymous catalog access allowed';
  exception when insufficient_privilege then null; end;
end $$;
rollback;
select 'passed: isolation, role escalation, aggregate writes, private media/evidence, anonymous denial; fixtures rolled back' as validation;
