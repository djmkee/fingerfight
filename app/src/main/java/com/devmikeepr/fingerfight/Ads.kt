package com.devmikeepr.fingerfight

import com.google.android.gms.ads.AdRequest
import com.google.android.gms.ads.AdView

/** Requests and loads a standard (non-personalized-by-default) banner ad. */
fun AdView.loadStandardAd() {
    loadAd(AdRequest.Builder().build())
}
