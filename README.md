# Finger Fight: Arena

A local same-screen multiplayer party game for Android, evolved from the
original Finger Fight concept.

## Modes

- **Chase Duel** (2–6 players): everyone places a finger on screen. After a
  hidden random delay, one player is secretly picked as the Hunter and must
  tag the others before the round timer runs out. Lifting your finger early
  is an instant elimination — even for the Hunter.
- **Reaction Duel** (2 players): hold a finger on your half of the screen.
  When the screen flashes "GO!", the first to lift wins the point. Lifting
  early is a false start and an automatic loss.

## Championships

Matches can be played as a single Quick round or as a Best of 3/5/7
championship. Points carry over between rounds and a live leaderboard is
shown after each one; whoever has the most points when the match ends is
crowned Champion (ties become co-champions).

## Project layout

This is a standard Gradle/Android Studio project:

- `app/` — the Android app module (Kotlin, View-based UI, no third-party
  game engine).
- `app/src/main/java/com/devmikeepr/fingerfight/` — game logic:
  - `ChaseArenaView` / `ReactionArenaView` — the multitouch game engines
    (custom `View`s handling raw `MotionEvent`s, collision detection,
    timers and animations).
  - `GameActivity` — round flow, scoring and the championship leaderboard.
  - `MainActivity`, `SetupActivity`, `HowToPlayActivity` — menu screens.
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

## Privacy

See `privacy.html` for the app's privacy policy.
