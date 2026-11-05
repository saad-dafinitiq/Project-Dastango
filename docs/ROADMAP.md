# Roadmap and known limitations

What the MVP does not do well yet, and the order things will be fixed in.

## Known limitations

| Limitation | Impact | Workaround today |
|---|---|---|
| **Every version copies every file** | Storage grows with each edit; R2's free 10 GB and the VM's disk will eventually fill. Since DST-35 (1080p films, finer intermediate clips) the delivered film is ~3.4x and the scene clips ~4x the size they were | Nightly backups hard-link unchanged files, so they cost little; the live store does not |
| **Free image quotas can all run out at once** (Cloudflare's 10,000 neurons a day, and Agnes' per-minute free tier, a temporary fallback) | A render completes with placeholder images (by design, and logged) | Wait for the daily reset |
| **Cloudflare's safety filter refuses some innocent prompts** | That image comes from the next provider | Automatic |
| **Renders are CPU-bound on the free VM** | ~4–5 min for a 45 s film before DST-35 (measured); 1080p output doubled render time on the laptop (77 s to 154 s for a 63 s film), so the server's time is expected to grow and is not yet measured. An A1 core is ~2.8× slower than a laptop's here | `SUPERSAMPLE=2` / `upscale.factor` in `config/video.yaml` would trade pan smoothness for speed; not taken |
| **Kokoro loads its model on a worker's first line** | ~30 s added to the first render after a restart | None |
| **An edit that rewrites the script starts its voices fresh** | Scene-only voice overrides do not carry over to a different script | Only the voice engine carries over |
| **Real motion and lip sync are untested end to end** | They are wired and unit-tested with mocked HTTP; every provider costs money per clip | Supersampled camera moves on stills |
| **Oracle may stop an idle Always Free VM** (CPU, network and memory all under 20% for 7 days) | A week's email notice, then the VM is stopped, not deleted | Start it again; or upgrade to Pay As You Go, which stays free within the limits |
| **Account gaps**: no email verification or reset, no two-factor, sessions not revocable per device in the UI, GitHub cannot be disconnected from the UI | Small at demo scale | Sign-ups closed by default; `python main.py users passwd` |
| **Python dependencies are minimums, not pins** | Two builds a week apart can resolve different versions | The image is tested in CI on every change |
| **Off-machine backups mirror only the newest films** | Restoring an older database dump pairs it with today's films | Older local snapshots are kept for 7 nights |

## Next

Ordered by what most threatens the product running unattended.

1. **Content-addressed storage for versions.** Store each distinct file once
   by hash and have versions refer to it; undo stays exact, storage grows
   with what actually changed.
2. **Reproducible builds.** Lock Python dependencies (pip-tools or uv) the way
   the web app already locks npm.
3. **Reviewer access.** A read-only showcase of finished films, or guest
   accounts with a per-account render quota, so the live site can be tried
   without opening sign-ups to the internet.
4. **Account polish.** Sessions listed and revocable per device; disconnect
   GitHub after setting a password.

## Later

- **Generated music** (ACE-Step) and **Chatterbox voices**: both need a
  PyTorch install (~2–3 GB), which on two ARM cores may be slow.
- **Real motion** (Veo 3.1 or fal) once there is a budget (already behind a
  configuration switch).
- **Sharing a film by link**, and a public gallery.
- **LISTEN/NOTIFY** for progress instead of polling rows, and per-route rate
  limiting, if traffic ever needs them.
- A **Content-Security-Policy** header.
