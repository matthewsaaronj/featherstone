// As-you-type scripture reference formatting, like a phone-number field.
//
// Type "Alma 34 14 15" and the box shows "Alma 34:14–15": the space after the
// chapter becomes ":" and the space after the first verse becomes "–". A faint
// guide behind the text shows the slots still to fill (___:___–___).
// Deleting never re-inserts punctuation. Applies to <input data-refmask>.
(function () {
  "use strict";

  // Book: optional leading 1–4, then letters (spaces, &, periods and dashes allowed
  // inside, e.g. "Doctrine and Covenants", "D&C", "JS-H"). Then the number part.
  var SPLIT = /^((?:[1-4]\s*)?[A-Za-z&'’.][A-Za-z&'’.\s—-]*?)(\s+|$)([\d\s:.\-–]*)$/;

  function parts(value) {
    var m = SPLIT.exec(value);
    if (!m) return null;
    var book = m[1].replace(/\s+/g, " ");
    var rest = m[3] || "";
    var nums = (rest.match(/\d+/g) || []).slice(0, 3);
    var trailing = /[\s:.\-–]$/.test(rest);
    return { book: book, spaced: m[2].length > 0, nums: nums, trailing: trailing };
  }

  function format(value) {
    var p = parts(value);
    if (!p || !p.nums.length) return null; // still typing the book: leave it alone
    var n = p.nums, out = p.book + " " + n[0];
    if (n.length === 1) return out + (p.trailing ? ":" : "");
    out += ":" + n[1];
    if (n.length === 2) return out + (p.trailing ? "–" : "");
    return out + "–" + n[2];
  }

  // What's still to fill, as faint placeholders.
  function guide(value) {
    if (!/[A-Za-z]/.test(value)) return "";
    var p = parts(value);
    if (!p) return "";
    var n = p.nums, slot = "___";
    if (!n.length) return (p.spaced ? "" : " ") + slot + ":" + slot + "–" + slot;
    if (n.length === 1) return (/:$/.test(value) ? "" : ":") + slot + "–" + slot;
    if (n.length === 2) return /–$/.test(value) ? slot : "–" + slot;
    return "";
  }

  function attach(input) {
    var wrap = document.createElement("span");
    wrap.className = "refmask";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    var ghost = document.createElement("span");
    ghost.className = "refmask-ghost";
    ghost.setAttribute("aria-hidden", "true");
    var typed = document.createElement("span");
    typed.className = "refmask-typed";
    var rest = document.createElement("span");
    rest.className = "refmask-rest";
    ghost.appendChild(typed);
    ghost.appendChild(rest);
    wrap.appendChild(ghost);
    // Line the guide up exactly with the input's text.
    var cs = getComputedStyle(input);
    ghost.style.font = cs.font;
    ghost.style.letterSpacing = cs.letterSpacing;
    ghost.style.paddingLeft = "calc(" + cs.paddingLeft + " + " + cs.borderLeftWidth + ")";
    ghost.style.paddingRight = "calc(" + cs.paddingRight + " + " + cs.borderRightWidth + ")";

    function paint() {
      var v = input.value;
      typed.textContent = v;
      rest.textContent = guide(v);
      // Hide the guide if the text has scrolled sideways (very long input).
      ghost.style.visibility = input.scrollWidth > input.clientWidth + 1 ? "hidden" : "visible";
    }

    input.addEventListener("input", function (e) {
      var type = e.inputType || "insertText";
      var atEnd = input.selectionStart === input.value.length;
      if (type.indexOf("delete") !== 0 && atEnd) {
        var f = format(input.value);
        if (f !== null && f !== input.value) {
          input.value = f;
          input.setSelectionRange(f.length, f.length);
        }
      }
      paint();
    });
    // Drop a dangling ":" or "–" when leaving the field or submitting.
    function tidy() { input.value = input.value.replace(/[\s:.\-–]+$/, ""); paint(); }
    input.addEventListener("blur", tidy);
    if (input.form) input.form.addEventListener("submit", tidy);
    paint();
  }

  document.querySelectorAll("input[data-refmask]").forEach(attach);
})();
