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
  - `GameActivity` — Reaction Duel round flow, scoring and the
    championship leaderboard.
  - `PickerActivity` / `PickerSetupActivity` — Finger Picker setup and
    reveal/ranking screen.
  - `MainActivity`, `HowToPlayActivity` — menu screens.
- `app/src/main/res/raw/` — short synthesized sound effects (no external
  assets).

## Building

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

## Privacy

See `privacy.html` for the app's privacy policy.
