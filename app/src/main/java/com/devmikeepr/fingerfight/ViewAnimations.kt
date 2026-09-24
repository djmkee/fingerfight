package com.devmikeepr.fingerfight

import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ValueAnimator
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
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

/** Smoothly grows a GONE/collapsed view open to its natural (wrap_content) height. */
fun View.expand(durationMs: Long = 220L) {
    val parentWidth = (parent as View).width
    measure(
        View.MeasureSpec.makeMeasureSpec(parentWidth, View.MeasureSpec.AT_MOST),
        View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED)
    )
    val targetHeight = measuredHeight
    layoutParams.height = 0
    visibility = View.VISIBLE
    ValueAnimator.ofInt(0, targetHeight).apply {
        duration = durationMs
        addUpdateListener {
            layoutParams.height = it.animatedValue as Int
            requestLayout()
        }
        addListener(object : AnimatorListenerAdapter() {
            override fun onAnimationEnd(animation: Animator) {
                layoutParams.height = ViewGroup.LayoutParams.WRAP_CONTENT
                requestLayout()
            }
        })
        start()
    }
}

/** Smoothly shrinks a visible view closed, then sets it GONE. */
fun View.collapse(durationMs: Long = 200L) {
    val initialHeight = height
    ValueAnimator.ofInt(initialHeight, 0).apply {
        duration = durationMs
        addUpdateListener {
            layoutParams.height = it.animatedValue as Int
            requestLayout()
        }
        addListener(object : AnimatorListenerAdapter() {
            override fun onAnimationEnd(animation: Animator) {
                visibility = View.GONE
                layoutParams.height = ViewGroup.LayoutParams.WRAP_CONTENT
            }
        })
        start()
    }
}
