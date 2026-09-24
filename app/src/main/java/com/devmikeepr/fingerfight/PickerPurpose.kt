package com.devmikeepr.fingerfight

enum class PickerPurpose(val labelRes: Int, val resultTemplateRes: Int) {
    WHO_PAYS(R.string.purpose_who_pays, R.string.purpose_who_pays_result),
    WHOS_IT(R.string.purpose_whos_it, R.string.purpose_whos_it_result),
    GOES_FIRST(R.string.purpose_goes_first, R.string.purpose_goes_first_result),
    TRUTH_OR_DARE(R.string.purpose_truth_or_dare, R.string.purpose_truth_or_dare_result),
    CUSTOM(R.string.purpose_custom, 0)
}
