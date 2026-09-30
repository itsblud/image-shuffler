# Shared moderation setup

The app uses a Supabase project for global image decisions. The browser receives only the project's public URL and publishable key. Never put a Supabase secret or service-role key in this static site.

1. Create a Supabase project and add one Auth user for the site owner. Disable public sign-ups after creating that account.
2. Run `supabase/shared-moderation.sql` in the Supabase SQL Editor.
3. In the SQL Editor, add the owner account as a moderator by replacing the example email in the commented `insert` statement at the bottom of the SQL file and running it.
4. Put the project's URL and **publishable** key in `shared-moderation.js`.
5. Publish a new Shuffler build. Existing browser-local moderation decisions can then be imported from the signed-in moderator device.

The `image_moderation` table is publicly readable so every visitor filters the same pool. Row Level Security allows writes only to user IDs listed in `moderators`. Sign in from Settings on each device that should be able to moderate. Visitors can read shared decisions without signing in.
