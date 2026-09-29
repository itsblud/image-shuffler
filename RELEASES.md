# Publishing Shuffler

The repository publishes its main branch through GitHub Pages.

- Current app: `https://itsblud.github.io/image-shuffler/`
- Release history: `https://itsblud.github.io/image-shuffler/releases.html`
- Frozen builds: `builds/<version>/`, each with its own app, fonts and catalog.
- Downloads: `downloads/SHUFFLER-<version>.zip`.

Before publishing changes, update the version shown in index.html and its asset query strings. Run the relevant tests, then:

```sh
python3 release_build.py 2.9.1 --notes 'Describe what changed and any limitations.'
```

The command refuses to overwrite an existing version. Commit the app changes together with the new build directory, ZIP, releases.json and releases.html, then push to main. Verify Pages has finished and that the live page shows the new version and expected controls. Do not edit previously frozen build directories. Each build.json records file checksums.

To use an older release, open its permanent link from All releases. These links do not depend on the local preview server. Catalog snapshots preserve image addresses, not the remotely hosted image bytes; Internet Archive outages can still affect every version.

Build 2.6 preserves the formerly published app from commit a5059d1. Build 2.8 preserves the recovered local test build, including its social-media toggle. Build 2.9 adds the release browser and publishes those controls.
