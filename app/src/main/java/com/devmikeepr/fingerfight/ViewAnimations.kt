package com.devmikeepr.fingerfight

import android.view.MotionEvent
import android.view.View
import android.view.animation.OvershootInterpolator

/** Adds a small tactile scale-down/scale-up on press, without swallowing the click. */
fun View.applyPressAnimation() {
    setOnTouchListener { v, event ->
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                v.animate().scaleX(0.96f).scaleY(0.96f).setDuration(100).start()
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                v.animate()
                    .scaleX(1f)
                    .scaleY(1f)
                    .setDuration(180)
                    .setInterpolator(OvershootInterpolator())
                    .start()
            }
        }
        false
    }
}

/** Fades/scales/slides a view into place -- used for a staggered menu entrance. */
fun View.popIn(delayMs: Long = 0L) {
    alpha = 0f
    scaleX = 0.85f
    scaleY = 0.85f
    translationY = 40f
    animate()
        .alpha(1f)
        .scaleX(1f)
        .scaleY(1f)
        .translationY(0f)
        .setStartDelay(delayMs)
        .setDuration(420)
        .setInterpolator(OvershootInterpolator(1.1f))
        .start()
}
