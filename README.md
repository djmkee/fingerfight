# Finger Fight: Arena

A local same-screen multiplayer party game for Android, evolved from the
original Finger Fight concept.

An earlier version of this app included a "Chase Duel" mode where one
player's finger had to physically chase and touch the others on screen.
Real playtesting showed the obvious problem: on a phone-sized screen,
fingers and hands just collide. It's been replaced with the Finger Picker
modes below, which reuse the same "everyone touches the screen at once"
idea without requiring anyone to move.

## Modes

- **Reaction Duel** (2 players): hold a finger on your half of the screen.
  When the screen flashes "GO!", the first to lift wins the point. Lifting
  early is a false start and an automatic loss. Played as a Quick round or
  a Best of 3/5/7 championship with a running scoreboard.
- **Finger Picker — Single Pick**: everyone places a finger anywhere on
  screen — no need to set a player count first. Once fingers stop arriving,
  the app spins through everyone and lands on one at random. Pick a purpose
  before you start: Who Pays, Who's It, Goes First, Truth or Dare, or your
  own custom text.
- **Finger Picker — Full Order**: the same mechanic, but instead of
  stopping after one pick it keeps going until every finger has a rank —
  useful for turn order, team drafts, chore rotations, or anything else
  that needs a random order decided on the spot.

## Look & feel

The whole app shares one visual language, built entirely from vector
drawables and Canvas effects (no external art assets, no custom fonts):

- **Animated backdrop** (`GlowBackgroundView`) — a slow-drifting gradient
  with soft glowing orbs, used behind every screen.
- **Main menu** — mode cards with gradient fills, hand-drawn icons, a
  staggered pop-in entrance animation, and a tactile scale-down on press
  (`ViewAnimations.kt`).
- **In-game effects** — every touch point has a neon glow (`Paint`
  shadow layers), finger-down triggers an expanding ripple ring, and
  Finger Picker bursts confetti when it reveals a winner. All shared via
  `BaseArenaView` so both game engines get them for free.

## Project layout

This is a standard Gradle/Android Studio project:

- `app/` — the Android app module (Kotlin, View-based UI, no third-party
  game engine).
- `app/src/main/java/com/devmikeepr/fingerfight/` — game logic:
  - `ReactionArenaView` — the Reaction Duel engine (custom `View` handling
    raw `MotionEvent`s, timers and false-start detection).
  - `PickerArenaView` — the Finger Picker engine shared by Single Pick and
    Full Order (dynamic headcount, lock-in grace period, decelerating spin
    animation, repeat-until-ranked elimination).
  - `BaseArenaView` — shared drawing/effects: glow circles, touch ripples,
    confetti bursts.
  - `GlowBackgroundView` — the animated menu/screen backdrop.
  - `ViewAnimations.kt` — reusable press-scale and pop-in animations.
  - `GameActivity` — Reaction Duel round flow, scoring and the
    championship leaderboard.
  - `PickerActivity` / `PickerSetupActivity` — Finger Picker setup and
    reveal/ranking screen.
  - `MainActivity`, `HowToPlayActivity` — menu screens.
- `app/src/main/res/raw/` — short synthesized sound effects (no external
  assets).

## Building (debug)

Open the project root in Android Studio (Giraffe or newer) and let it sync,
or from the command line:

```
./gradlew assembleDebug
```

Requires the Android SDK (compileSdk 34) and network access to Google's
Maven repository to resolve AndroidX/Material dependencies — this couldn't
be verified in the sandbox this was written in (no access to
`dl.google.com`), so do a first build/run in Android Studio before relying
on it. The Kotlin game-logic classes (the arena views, models and sound
manager) were type-checked against real Android framework classes during
development.

Playtest note: Reaction Duel works with the emulator's mouse pointer (one
finger). The Finger Picker modes need genuine multitouch to test properly —
use a real device, or the emulator's Ctrl-drag two-finger gesture for a
2-finger sanity check.

## Building a signed release (for the Play Store or direct install)

Release builds are unsigned by default (Gradle just skips signing if it
can't find a keystore) so debug work is never blocked. To produce a real,
installable release build:

**1. Generate a keystore (once, on your own machine — never commit this file):**

```
keytool -genkeypair -v -keystore fingerfight-release.jks -keyalg RSA -keysize 2048 -validity 10000 -alias fingerfight
```

It'll ask for a keystore password, your name/org details, and a key
password (you can reuse the keystore password). Keep this file and its
passwords safe — losing it means you can never publish an update to the
same app listing again.

**2. Create `keystore.properties` in the project root** (same folder as
`settings.gradle.kts`) — this file is already git-ignored:

```properties
storeFile=../fingerfight-release.jks
storePassword=YOUR_KEYSTORE_PASSWORD
keyAlias=fingerfight
keyPassword=YOUR_KEY_PASSWORD
```

(`storeFile` is relative to the `app/` module, so `../` if the `.jks` sits
next to `settings.gradle.kts` as in the command above — adjust the path if
you put it somewhere else.)

**3. Build:**

```
./gradlew bundleRelease   # produces app/build/outputs/bundle/release/app-release.aab (Play Store)
./gradlew assembleRelease # produces app/build/outputs/apk/release/app-release.apk (direct install/testing)
```

Both are signed with your keystore and have code/resource shrinking
enabled (`isMinifyEnabled` / `isShrinkResources`), so they're
production-sized, not debug builds.

**Before submitting to the Play Store**, you'll also need (outside of what
I can generate here): a 512x512 hi-res icon (Android Studio's **Image
Asset** tool can export one from the existing adaptive icon), a 1024x500
feature graphic, a few screenshots, and a privacy policy URL — `privacy.html`
in this repo already covers the last one, just host it somewhere public
(GitHub Pages works) and paste that URL into the Play Console listing.

## Privacy

See `privacy.html` for the app's privacy policy.
