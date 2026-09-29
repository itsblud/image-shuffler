# SHUFFLER build 2.9

Catalog: 30,787 archived image candidates from the September 29 harvest.

Changes in this build:
- Publish the social-media control and add Settings → All releases with the current build number.
- Preserve builds 2.6, 2.8 and 2.9 with separate app/catalog snapshots and ZIP downloads.
- Add FortuneCity and Rave.ca registration plus resumable yearly collection for all five requested sources. Live archive queries timed out; no new images were added.
- Add a Social media posts toggle (on by default), excluding dedicated social-network sources such as BlackPlanet when off. This is source-based, not visual classification.
- Apply changed filters to preload entries and navigation history; replace the current image if excluded.
- Display the first ready image before filling the three-image lookahead buffer.
- Ignore overlapping Next requests while an image is loading.
- Selecting Africa clears the other regions; selecting another region clears Africa.
- Keep Africa out of the default mixed-region selection.
- Prevent an old preload operation from clearing the current operation after settings change.

Checks: social-source inclusion/exclusion, preference persistence, history/preload clearing and current-image replacement regression checks; JavaScript syntax; manifest and region count consistency; archive URL host and regional year bounds; browser image loading and Africa selection/year switching.

Known limitations: Europe has zero entries and Africa has one. Image URLs are archival candidates, not 30,787 verified working images; remote timeouts and rejected small images can cause delays.

Local preview: http://127.0.0.1:8765/ while the preview server is running.
This package contains static web files. Serve it with a web server, rather than opening index.html directly.
