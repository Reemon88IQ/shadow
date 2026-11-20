# Shadow privacy policy

*Last updated: 19 November 2026*

Shadow is a free, open-source Windows desktop app that renders video scenes to MP4 files on your own computer and can
upload them to your own YouTube channel. It has no servers, no accounts and no analytics. This page explains what data
Shadow touches and where it goes.

## What Shadow sends over the internet

| When | To whom | What |
|---|---|---|
| Shadow starts | GitHub (api.github.com) | A request for the latest release number, to show "Update to vX.Y". No personal data. |
| A scene loads web resources | The sites the scene itself names (for example a CDN for three.js or fonts) | Normal web requests made by the scene. |
| You press **Sign in...** in the YouTube dialog | Google (accounts.google.com, oauth2.googleapis.com) | The standard Google sign-in. You type your password on Google's own page; Shadow never sees it. |
| You press **Upload** | YouTube Data API (googleapis.com) | The video file you chose and its title, description, tags, category, privacy and "Made for Kids" setting, the "altered or synthetic content" label and publish time if set, and the thumbnail image if one is set. |
| You use the Assistant panel | Anthropic, through your own installed Claude Code | Your chat messages. Only when you use that panel. |

Nothing else is sent anywhere.

## YouTube and Google user data

Shadow uses the YouTube API Services. By uploading with Shadow you agree to the
[YouTube Terms of Service](https://www.youtube.com/t/terms), and Google's use of your data is governed by the
[Google Privacy Policy](https://policies.google.com/privacy).

- **Permissions requested:** `youtube.upload` (to upload the videos you choose) and `youtube.readonly` (to read your
  channel's name and ID, so the dialog can show which channel you are uploading to). Shadow does not read, change or
  delete your existing videos, comments, playlists or any other data.
- **What is stored:** after you sign in, Shadow stores your channel's name and ID and the OAuth refresh/access tokens in
  one file on your own computer: `%USERPROFILE%\.shadow_youtube_tokens.json`. It is never sent anywhere except back to
  Google to upload.
- **No sharing:** no YouTube or Google data is sent to the developer of Shadow or to any third party. There is no
  developer server.
- **No other use:** the data is used only to upload the videos you choose, when you press Upload.
- **Deleting your data:** delete `%USERPROFILE%\.shadow_youtube_tokens.json` (or uninstall Shadow and delete it), and
  revoke Shadow's access at [Google account permissions](https://myaccount.google.com/permissions) → Shadow → Remove access.
  Shadow keeps no other copy.

Shadow's use of information received from Google APIs adheres to the
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements.

## Files on your computer

Shadow keeps its settings in `%USERPROFILE%\.shadow_renderer.json` and a log in `%USERPROFILE%\shadow.log`. Your scenes and
videos stay where you put them.

## Contact

Questions or requests: open an issue at [github.com/Reemon88IQ/shadow/issues](https://github.com/Reemon88IQ/shadow/issues).
