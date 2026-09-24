package com.devmikeepr.fingerfight

import android.content.Context

object Prefs {
    private const val NAME = "fingerfight_prefs"
    private const val KEY_SOUND = "sound_enabled"
    private const val KEY_HAPTICS = "haptics_enabled"

    fun isSoundEnabled(context: Context): Boolean = prefs(context).getBoolean(KEY_SOUND, true)

    fun setSoundEnabled(context: Context, value: Boolean) {
        prefs(context).edit().putBoolean(KEY_SOUND, value).apply()
    }

    fun isHapticsEnabled(context: Context): Boolean = prefs(context).getBoolean(KEY_HAPTICS, true)

    fun setHapticsEnabled(context: Context, value: Boolean) {
        prefs(context).edit().putBoolean(KEY_HAPTICS, value).apply()
    }

    private fun prefs(context: Context) =
        context.getSharedPreferences(NAME, Context.MODE_PRIVATE)
}
