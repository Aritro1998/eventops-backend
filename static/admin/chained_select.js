/*
 * Generic client-side "chained dropdown" filter for Django admin forms.
 *
 * Any <select> rendered with a `data-chained-parent="id_xxx"` attribute
 * (see core/admin_widgets.py's ChainedSelect) gets its <option>s hidden
 * or shown based on whether their `data-parent-id` matches the current
 * value of the parent <select> with that id. No AJAX call — every
 * option is already in the page, just tagged with which parent it
 * belongs to, since the option counts here are small (a handful of
 * Spaces/Events, not thousands).
 *
 * Fails safe: if the expected elements aren't found (e.g. Django's admin
 * markup changes in a future version), this does nothing rather than
 * throwing — worse case is "no filtering happens", not a broken page.
 */
(function () {
    function applyFilter(select, parentSelect) {
        var parentValue = parentSelect.value;
        var currentValue = select.value;
        var currentStillVisible = false;

        Array.prototype.forEach.call(select.options, function (option) {
            if (!option.value) {
                // Always keep the blank "---------" choice visible.
                return;
            }
            var matches = !parentValue || option.dataset.parentId === parentValue;
            option.hidden = !matches;
            if (matches && option.value === currentValue) {
                currentStillVisible = true;
            }
        });

        if (!currentStillVisible) {
            select.value = "";
        }
    }

    function wireUp(select) {
        var parentId = select.dataset.chainedParent;
        var parentSelect = parentId && document.getElementById(parentId);
        if (!parentSelect) {
            return;
        }
        parentSelect.addEventListener("change", function () {
            applyFilter(select, parentSelect);
        });
        // Apply immediately too — covers editing an existing object where
        // the parent field already has a value on page load.
        applyFilter(select, parentSelect);
    }

    document.addEventListener("DOMContentLoaded", function () {
        var selects = document.querySelectorAll("select[data-chained-parent]");
        Array.prototype.forEach.call(selects, wireUp);
    });
})();
