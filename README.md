# Stream Failsafe

Maintained by [bigouncereal2-jpg](https://github.com/bigouncereal2-jpg).

Stream Failsafe screens outgoing Windows display capture for nudity, sexual content, and graphic imagery before sending approved frames to OBS. A detection blacks out the source until you re-arm it. Everything runs locally.

[Download the Windows ZIP](https://github.com/bigouncereal2-jpg/no-nsfw-livestream/releases/latest)

## Set up OBS

1. Extract the Windows package, open `Stream Failsafe.exe`, and click **OBS setup** for the in-app guide. The output starts black.
2. Click **Copy URL**. In a fresh OBS scene, put a full-canvas black Color Source under a Browser Source and paste the URL. Use your canvas dimensions for the Browser Source. The app shows **Connected** once a browser source opens the feed.
3. Remove direct captures of the monitored display from the broadcast scene, including nested scenes. If the preview appears in the capture, disable the preview or move OBS to a different monitor.
4. Pick a display and select **Arm protection**. Make a local OBS recording to verify normal video, **Test blackout**, and **Ctrl+Shift+F10**. After a blackout, click **Arm protection** again when ready.

## Stream to Twitch or TikTok LIVE

- **Twitch** uses your normal OBS stream settings and the protected scene.
- **TikTok LIVE** can take the protected scene through [OBS Virtual Camera](https://obsproject.com/kb/virtual-camera-guide) where your account offers it. Select the camera as your TikTok video source and configure audio in TikTok. Accounts with RTMP access can also send the scene directly from OBS.

## Timing and coverage

The app processes the selected display at up to 30 fps. Detection happens before release. A detected frame is withheld. Capture stalls black out the source, and a closed app disconnects it. The models can miss content or flag harmless images. Separate OBS sources and audio do not pass through this filter. The app does not guarantee a 1 ms response.

The local OBS integration was tested with a simulated flagged frame, a stalled feed, and the manual panic hotkey. TikTok account routing has not been tested.

## Build from source

Run `Build.ps1` on Windows with Python 3.12 installed. It uses an isolated environment and verifies the SHA-256 digests of both model files. The executable is written to `dist/Stream Failsafe.exe`.

The application source is available under the [GNU Affero General Public License v3](LICENSE). See [Models.txt](Models.txt) for the bundled models and upstream licenses.
