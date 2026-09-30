-- Run this once in the Supabase SQL editor after creating the project.
-- Create your Auth user first, then add its UUID to public.moderators as shown below.

create table if not exists public.moderators (
  user_id uuid primary key references auth.users(id) on delete cascade
);

create table if not exists public.image_moderation (
  image_id text primary key,
  archive_url text,
  omitted boolean not null default false,
  nsfw boolean not null default false,
  updated_at timestamptz not null default now(),
  constraint image_moderation_has_decision check (omitted or nsfw)
);

alter table public.moderators enable row level security;
alter table public.image_moderation enable row level security;

create or replace function public.is_shuffler_moderator()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.moderators
    where user_id = (select auth.uid())
  );
$$;

revoke all on function public.is_shuffler_moderator() from public;
grant execute on function public.is_shuffler_moderator() to authenticated;

grant select on public.image_moderation to anon, authenticated;
grant insert, update, delete on public.image_moderation to authenticated;

drop policy if exists "Anyone can read moderation decisions" on public.image_moderation;
create policy "Anyone can read moderation decisions"
  on public.image_moderation for select to anon, authenticated
  using (true);

drop policy if exists "Only moderators can change decisions" on public.image_moderation;
create policy "Only moderators can change decisions"
  on public.image_moderation for all to authenticated
  using ((select public.is_shuffler_moderator()))
  with check ((select public.is_shuffler_moderator()));

-- After creating your Supabase Auth user, run this with your sign-in email:
-- insert into public.moderators (user_id)
-- select id from auth.users where email = 'you@example.com'
-- on conflict do nothing;
