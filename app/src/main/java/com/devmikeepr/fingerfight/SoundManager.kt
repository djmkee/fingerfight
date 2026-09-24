package com.devmikeepr.fingerfight

import android.content.Context
import android.media.AudioAttributes
import android.media.SoundPool
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

class SoundManager(context: Context) {

    private val appContext = context.applicationContext

    private val soundPool: SoundPool = SoundPool.Builder()
        .setMaxStreams(4)
        .setAudioAttributes(
            AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_GAME)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                .build()
        )
        .build()

    private val revealSoundId = soundPool.load(appContext, R.raw.reveal, 1)
    private val tagSoundId = soundPool.load(appContext, R.raw.tag, 1)
    private val winSoundId = soundPool.load(appContext, R.raw.round_win, 1)

    private val vibrator: Vibrator = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        val manager = appContext.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager
        manager.defaultVibrator
    } else {
        @Suppress("DEPRECATION")
        appContext.getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
    }

    var soundEnabled: Boolean = Prefs.isSoundEnabled(appContext)
    var hapticsEnabled: Boolean = Prefs.isHapticsEnabled(appContext)

    fun playReveal() {
        if (soundEnabled) soundPool.play(revealSoundId, 1f, 1f, 1, 0, 1f)
        vibrateOneShot(80)
    }

    fun playTag() {
        if (soundEnabled) soundPool.play(tagSoundId, 1f, 1f, 1, 0, 1f)
        vibrateOneShot(40)
    }

    fun playRoundWin() {
        if (soundEnabled) soundPool.play(winSoundId, 1f, 1f, 1, 0, 1f)
        vibratePattern(longArrayOf(0, 60, 60, 120))
    }

    private fun vibrateOneShot(ms: Long) {
        if (!hapticsEnabled) return
        vibrator.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE))
    }

    private fun vibratePattern(pattern: LongArray) {
        if (!hapticsEnabled) return
        vibrator.vibrate(VibrationEffect.createWaveform(pattern, -1))
    }

    fun release() {
        soundPool.release()
    }
}
