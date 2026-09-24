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

Four scored duels, each playable as a Quick round or a Best of 3/5/7
championship with a running scoreboard:

- **Reaction Duel** (2 players): hold a finger on your half of the screen.
  When the screen flashes "GO!", the first to lift wins the point. Lifting
  early is a false start and an automatic loss.
- **Reaction Rumble** (2-8 players): the multiplayer sibling of Reaction
  Duel — everyone holds a finger at once, first to lift after "GO!" wins.
  Jumping the gun only disqualifies you for that round, not everyone else.
- **Tap Battle** (2 players): place a finger to ready up, then tap your
  half as fast as you can after the countdown. First to the target tap
  count wins the round.
- **Endurance** (2-8 players, player count configurable at setup): everyone
  holds a finger down at once. As soon as all fingers are down, the clock
  starts — whoever lifts first is out, and the last finger standing wins.

And three "no setup needed" Finger Picker utilities, all sharing one
dynamic-headcount engine (no player count to configure — just however many
fingers land on the screen):

- **Single Pick**: everyone places a finger, the app spins through them all
  and lands on one at random. Pick a purpose before you start: Who Pays,
  Who's It, Goes First, Truth or Dare, or your own custom text.
- **Full Order**: the same mechanic, but instead of stopping after one pick
  it keeps going until every finger has a rank — useful for turn order,
  team drafts, chore rotations, or anything else that needs a random order
  decided on the spot.
- **Team Split**: everyone places a finger and the app randomly, evenly
  divides the group into Team A and Team B.

## Look & feel

The whole app shares one visual language, built entirely from vector
drawables and Canvas effects (no external art assets, no custom fonts):

- **Animated backdrop** (`GlowBackgroundView`) — a slow-drifting gradient
  with soft glowing orbs, used behind every screen.
- **Main menu** — a bento-grid layout (one large featured card, plus a 2x2
  grid of smaller mode tiles) with gradient fills, hand-drawn icons, a
  staggered pop-in entrance animation, and a tactile scale-down + real
  haptic tick on press (`ViewAnimations.kt`).
- **Per-mode color identity** (`ModeStyle.kt`) — each duel and Finger Picker
  has its own accent color (cyan for Reaction, amber for Rumble, orange for
  Tap Battle, green for Endurance, magenta for Finger Picker). Setup and
  How to Play screens use it to tint icons, card outlines and primary
  buttons, and a gradient hero banner (reusing the same card art from the
  main menu) tops every setup screen so the inner screens read as an
  extension of the main menu instead of a flat gray form.
- **In-game effects** — every touch point has a neon glow (`Paint`
  shadow layers), finger-down triggers an expanding ripple ring, and
  Finger Picker/Reaction Rumble burst confetti on a win. All shared via
  `BaseArenaView` so every arena gets them for free.
- **Setup screens** — grouped into color-accented cards instead of a flat
  scrolling list, with a sticky "Start" button pinned above the ad banner
  so it's always reachable without scrolling.
- **How to Play** — expandable per-mode accordion cards
  (`View.expand()`/`collapse()` in `ViewAnimations.kt`), each tinted with
  its mode's accent color, instead of one long wall of text.
- **Toggle buttons** (match length, pick type, order presets) autosize their
  text so labels like "Best of 3" or "Team Split" always fit instead of
  clipping on narrow screens.

## Project layout

This is a standard Gradle/Android Studio project:

- `app/` — the Android app module (Kotlin, View-based UI, no third-party
  game engine).
- `app/src/main/java/com/devmikeepr/fingerfight/` — game logic:
  - `ReactionArenaView`, `ReactionRumbleArenaView`, `TapBattleArenaView`,
    `EnduranceArenaView` — the four duel engines (custom `View`s handling
    raw `MotionEvent`s, timers and win conditions).
  - `PickerArenaView` — the Finger Picker engine shared by Single Pick,
    Full Order and Team Split (dynamic headcount, lock-in grace period,
    decelerating spin animation).
  - `BaseArenaView` — shared drawing/effects: glow circles, touch ripples,
    confetti bursts, shrink-and-fade eliminations.
  - `GlowBackgroundView` — the animated menu/screen backdrop.
  - `ViewAnimations.kt` — reusable press-scale, pop-in and
    expand/collapse (accordion) animations.
  - `ModeStyle.kt` — per-mode accent color/icon/hero-art lookup used by
    `SetupActivity` and `HowToPlayActivity` to color each screen.
  - `GameActivity` — duel round flow, scoring and the championship
    leaderboard (mode-agnostic across all four duels).
  - `SetupActivity` — duel setup (rounds, and player count for
    Endurance/Reaction Rumble).
  - `PickerActivity` / `PickerSetupActivity` — Finger Picker setup and
    reveal/ranking/team screen.
  - `MainActivity`, `HowToPlayActivity` — menu screens.
  - `FingerFightApplication` / `Ads.kt` — AdMob initialization and banner
    loading (see "AdMob banners" below).
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

## AdMob banners

Every screen has a banner ad slot docked to the bottom edge (the gameplay
screens constrain the play area to sit above it, so it never overlaps
touches). The wiring is all in place and currently points at **Google's
official test app ID and ad unit ID** (`admob_app_id` /
`admob_banner_ad_unit_id` in `strings.xml`) — these only ever serve test
ads, so the app runs and shows ads immediately with no AdMob account
needed for development.

**Before publishing**, you must swap both for your own real ids:

1. Create an AdMob account at [admob.google.com](https://admob.google.com)
   and register the app to get a real **App ID**
   (`ca-app-pub-XXXXXXXXXXXXXXXX~YYYYYYYYYY`).
2. Create a **Banner** ad unit for it to get a real **Ad Unit ID**
   (`ca-app-pub-XXXXXXXXXXXXXXXX/ZZZZZZZZZZ`).
3. Replace the two test values in `strings.xml` with your real ones.

Publishing with the test ids in place is against AdMob policy and can get
a real account suspended, so don't skip this step. The dependency itself
(`com.google.android.gms:play-services-ads`) is on Google's Maven, same as
every other AndroidX/Material dependency here, so it needs the same
network access as the rest of the build.

## Privacy

See `privacy.html` for the app's privacy policy.
