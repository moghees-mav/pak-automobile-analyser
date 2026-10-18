/* PakWheels Market Explorer - front-end controller.
 * Talks to the Flask API, renders the ETA panel, the live job progress,
 * the Plotly scatter chart and the result tables. No build step required. */
(function () {
  "use strict";

  var POLL_MS = 1000;
  var PREVIEW_ROWS = 200;

  var DEAL_ORDER = ["Great Deal", "Good Deal", "Fair Price", "Overpriced", "Unknown"];
  var DEAL_COLORS = {
    "Great Deal": "#1a9850",
    "Good Deal": "#66bd63",
    "Fair Price": "#4c78a8",
    "Overpriced": "#d73027",
    "Unknown": "#9aa5b1"
  };
  var DEAL_BADGES = {
    "Great Deal": "deal-great",
    "Good Deal": "deal-good",
    "Fair Price": "deal-fair",
    "Overpriced": "deal-overpriced",
    "Unknown": "deal-unknown"
  };

  var state = {
    catalogue: null,
    estimate: null,
    jobId: null,
    pollTimer: null,
    payload: null,
    snapshot: null,
    previewFilter: "",
    dealFilter: "",
    editing: null,
    chartOptions: {
      x: "year",
      y: "price_lacs",
      color: "variant_family",
      trend: true,
      logY: false,
      includeFeatured: true
    }
  };

  function $(id) { return document.getElementById(id); }

  function showError(message) {
    var banner = $("error-banner");
    banner.textContent = message;
    banner.className = "banner banner-error";
    banner.hidden = false;
  }

  function showInfo(message) {
    var banner = $("error-banner");
    banner.textContent = message;
    banner.className = "banner banner-info";
    banner.hidden = false;
  }

  function clearError() { $("error-banner").hidden = true; }

  function humanDuration(seconds) {
    if (seconds === null || seconds === undefined) { return "-"; }
    var total = Math.round(Number(seconds));
    if (total < 60) { return total + " s"; }
    var minutes = Math.floor(total / 60);
    var rest = total % 60;
    if (minutes < 60) { return minutes + " min " + rest + " s"; }
    var hours = Math.floor(minutes / 60);
    return hours + " h " + (minutes % 60) + " min";
  }

  function formatPkr(value) {
    if (value === null || value === undefined) { return "-"; }
    return "PKR " + Math.round(Number(value)).toLocaleString("en-US");
  }

  function formatLacs(value) {
    if (value === null || value === undefined) { return "n/a"; }
    return Number(value).toFixed(2) + " lacs";
  }

  function formatResidualLacs(value) {
    if (value === null || value === undefined) { return "n/a"; }
    var lacs = Number(value) / 100000;
    return (lacs > 0 ? "+" : "") + lacs.toFixed(2) + " lacs";
  }

  function dealColor(rating) { return DEAL_COLORS[rating] || DEAL_COLORS.Unknown; }
  function dealBadgeClass(rating) { return DEAL_BADGES[rating] || DEAL_BADGES.Unknown; }

  function escapeHtml(text) {
    return String(text === null || text === undefined ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function handleResponse(response) {
    return response.json().catch(function () { return {}; }).then(function (data) {
      if (!response.ok) { throw new Error(data.error || ("Request failed (" + response.status + ")")); }
      return data;
    });
  }

  function getJSON(url) {
    return fetch(url, { headers: { "Accept": "application/json" } }).then(handleResponse);
  }

  function postJSON(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(body || {})
    }).then(handleResponse);
  }

  function setBusy(button, busy, label) {
    if (busy) {
      button.dataset.label = button.dataset.label || button.textContent;
      button.disabled = true;
      button.innerHTML = '<span class="spinner"></span>' + escapeHtml(label || "Working…");
    } else {
      button.disabled = false;
      button.innerHTML = escapeHtml(button.dataset.label || label || button.textContent);
    }
  }

  // ---------------------------------------------------------------- catalogue

  function loadCatalogue(refresh) {
    var badge = $("badge-catalogue");
    badge.textContent = "catalogue: loading…";
    return getJSON("/api/catalogue" + (refresh ? "?refresh=1" : "")).then(function (data) {
      state.catalogue = data;
      var select = $("make");
      select.innerHTML = "";
      (data.makes || []).forEach(function (make) {
        select.appendChild(new Option(make.name + " (" + make.models.length + ")", make.slug));
      });
      select.appendChild(new Option("Other / not listed…", "__custom__"));
      var stamp = data.built_at ? " · " + data.built_at.slice(0, 10) : "";
      badge.textContent = "catalogue: " + data.source + " (" + data.makes.length + " makes, " +
        data.total_models + " models)" + stamp;
      onMakeChange();
    }).catch(function (error) {
      badge.textContent = "catalogue: unavailable";
      showError("Could not load the make/model catalogue: " + error.message +
        " — you can still type make and model slugs manually.");
      $("make").innerHTML = "";
      $("make").appendChild(new Option("Other / not listed…", "__custom__"));
      onMakeChange();
    });
  }

  function onMakeChange() {
    var slug = $("make").value;
    var custom = slug === "__custom__";
    $("make-custom").hidden = !custom;
    $("model").innerHTML = "";

    if (custom) {
      $("model").hidden = true;
      $("model-custom").hidden = false;
      state.estimate = null;
      $("btn-start").disabled = false;
      return;
    }

    $("model-custom").hidden = true;
    $("model").hidden = false;
    var make = (state.catalogue && state.catalogue.makes || []).filter(function (item) {
      return item.slug === slug;
    })[0];
    if (make) {
      make.models.forEach(function (model) {
        $("model").appendChild(new Option(model.name, model.slug));
      });
    }
    $("model").disabled = !make || make.models.length === 0;
    state.estimate = null;
    $("btn-start").disabled = $("model").disabled;
  }

  function currentSelection() {
    var custom = $("make").value === "__custom__";
    var make = custom ? ($("make-custom").value || "").trim() : $("make").value;
    var model = custom ? ($("model-custom").value || "").trim() : $("model").value;
    if (!make || !model) {
      showError("Please choose both a make and a model (or type their slugs).");
      return null;
    }
    return { make: make, model: model };
  }

  function buildBody() {
    var selection = currentSelection();
    if (!selection) { return null; }
    return {
      make: selection.make,
      model: selection.model,
      pages: $("all-pages").checked ? "all" : $("pages").value,
      delay: Number($("delay").value) || 2
    };
  }

  // ----------------------------------------------------------------- estimate

  function requestEstimate() {
    var body = buildBody();
    if (!body) { return; }
    clearError();
    $("estimate-placeholder").hidden = true;
    $("estimate-body").hidden = false;
    setBusy($("btn-estimate"), true, "Measuring…");
    postJSON("/api/estimate", body).then(function (data) {
      state.estimate = data;
      renderEstimate(data);
      $("btn-start").disabled = false;
    }).catch(function (error) {
      showError(error.message);
      $("estimate-body").hidden = true;
      $("estimate-placeholder").hidden = false;
    }).then(function () {
      setBusy($("btn-estimate"), false, "Estimate time");
    });
  }

  function renderEstimate(data) {
    $("est-total").textContent = (data.total_results === null || data.total_results === undefined)
      ? "unknown" : Number(data.total_results).toLocaleString("en-US");
    $("est-available").textContent = data.available_pages ? data.available_pages : "unknown";
    $("est-pages").textContent = data.pages_to_scrape;
    $("est-latency").textContent = data.latency_s + " s";
    $("est-perpage").textContent = data.seconds_per_page + " s";
    $("est-eta").textContent = data.eta_human + " (" + data.eta_range_human + ")";
    $("est-finish").textContent = "Started " + (data.started_at_iso || "-") +
      " · delay " + data.delay + " s/request → estimated finish around " +
      (data.finish_at_iso || "-") + (data.measured ? "" : " (rough estimate)");

    var notes = $("est-notes");
    if (data.notes) { notes.textContent = data.notes; notes.hidden = false; }
    else { notes.hidden = true; }

    var samples = data.samples || [];
    $("est-sample-wrap").hidden = samples.length === 0;
    $("est-sample-count").textContent = samples.length;
    renderTable($("est-sample-table"), [
      { key: "year", label: "Year" },
      { key: "title", label: "Title" },
      { key: "price_lacs", label: "Price (lacs)" },
      { key: "mileage_km", label: "Mileage (km)" },
      { key: "location", label: "City" },
      { key: "transmission", label: "Transmission" }
    ], samples);
  }

  // ------------------------------------------------------------------- scrape

  function startScrape() {
    var body = buildBody();
    if (!body) { return; }
    clearError();
    $("log").innerHTML = "";
    $("results-panel").hidden = true;
    $("btn-start").disabled = true;
    setBusy($("btn-start"), true, "Starting…");

    body.pages_available = (state.estimate && state.estimate.available_pages) || null;
    body.estimate = state.estimate ? {
      total_results: state.estimate.total_results,
      pages_to_scrape: state.estimate.pages_to_scrape,
      latency_s: state.estimate.latency_s,
      eta_seconds: state.estimate.eta_seconds
    } : {};

    postJSON("/api/scrape", body).then(function (snapshot) {
      state.jobId = snapshot.id;
      $("btn-cancel").disabled = false;
      renderProgress(snapshot);
      if (state.pollTimer) { clearInterval(state.pollTimer); }
      state.pollTimer = setInterval(pollJob, POLL_MS);
    }).catch(function (error) {
      showError(error.message);
      $("btn-start").disabled = false;
    }).then(function () {
      setBusy($("btn-start"), false, "Start scrape");
    });
  }

  function pollJob() {
    if (!state.jobId) { return; }
    getJSON("/api/job/" + state.jobId + "?log=40").then(function (snapshot) {
      renderProgress(snapshot);
      if (snapshot.state === "done" || snapshot.state === "error" || snapshot.state === "cancelled") {
        if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
        $("btn-cancel").disabled = true;
        $("btn-start").disabled = false;
        if (snapshot.state === "done") { loadResults(snapshot); }
        if (snapshot.state === "error") { showError(snapshot.error || "The scrape failed."); }
        if (snapshot.state === "cancelled") { showError("Scrape cancelled."); }
      }
    }).catch(function (error) {
      if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
      showError(error.message);
      $("btn-cancel").disabled = true;
      $("btn-start").disabled = false;
    });
  }

  function renderProgress(snapshot) {
    state.snapshot = snapshot;
    $("progress-fill").style.width = (snapshot.progress_pct || 0) + "%";

    var pill = $("prog-state");
    pill.textContent = snapshot.state;
    pill.className = "pill " + (snapshot.state === "running" ? "running" :
      (snapshot.state === "done" ? "done" : (snapshot.state === "error" ? "error" : "")));

    $("prog-pages").textContent = "page " + snapshot.pages_done + " / " + (snapshot.pages_total || 0);
    $("prog-listings").textContent = snapshot.listings + " listings";
    $("prog-elapsed").textContent = snapshot.elapsed_human + " elapsed";
    $("prog-eta").textContent = snapshot.eta_remaining_human
      ? "~" + snapshot.eta_remaining_human + " left" : "";

    var list = $("log");
    list.innerHTML = "";
    (snapshot.events || []).forEach(function (event) {
      var item = document.createElement("li");
      if (event.stage === "error") { item.className = "error"; }
      item.textContent = event.at + "  " + event.message;
      list.appendChild(item);
    });
    list.scrollTop = list.scrollHeight;
  }

  // ------------------------------------------------------------------ results

  function loadResults(snapshot) {
    if (!snapshot || !snapshot.id) { return; }
    getJSON("/api/result/" + snapshot.id).then(function (payload) {
      state.payload = payload;
      state.snapshot = snapshot;
      $("results-panel").hidden = false;
      renderResultStats(payload, snapshot);
      wireDownloads(snapshot, payload);
      renderPreview(payload);
      drawChart();
      $("results-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    }).catch(function (error) { showError(error.message); });
  }

  function renderResultStats(payload, snapshot) {
    var stats = payload.stats || {};
    var dealCounts = stats.deal_counts || {};
    var priceRange = (stats.price_min_lacs === undefined)
      ? "-" : stats.price_min_lacs + " – " + stats.price_max_lacs + " lacs";
    var yearRange = stats.year_min ? stats.year_min + " – " + stats.year_max : "-";
    var cards = [
      ["Listings", stats.count],
      ["Price range", priceRange],
      ["Median price", formatPkr(stats.price_median)],
      ["Years", yearRange],
      ["Median mileage", stats.mileage_median_km
        ? Number(stats.mileage_median_km).toLocaleString("en-US") + " km" : "-"],
      ["Featured ads", stats.featured_count],
      ["Great deals", dealCounts["Great Deal"] || 0],
      ["Overpriced", dealCounts["Overpriced"] || 0],
      ["FMV coverage", (stats.fmv_available_count || 0) + " / " + (stats.count || 0)],
      ["Duplicates removed", snapshot.duplicates_dropped],
      ["Pages scraped", snapshot.pages_done + " / " + (snapshot.pages_total || 0)],
      ["Manual corrections", manualEdits(payload)]
    ];
    var container = $("result-stats");
    container.innerHTML = "";
    cards.forEach(function (pair) {
      var box = document.createElement("div");
      box.className = "stat";
      box.innerHTML = '<span class="stat-label">' + escapeHtml(pair[0]) +
        '</span><span class="stat-value">' + escapeHtml(pair[1]) + "</span>";
      container.appendChild(box);
    });
  }

  function wireDownloads(snapshot, payload) {
    var files = snapshot.downloads || {};
    $("dl-csv").href = files.csv || "#";
    $("dl-json").href = files.json || "#";
    $("dl-html").href = files.html || "#";
    $("dl-meta").href = files.meta || "#";
    $("link-source").href = (payload.meta && payload.meta.source_url) || snapshot.source_url || "#";
  }

  function manualEdits(payload) {
    return (payload && payload.meta && payload.meta.manual_edits) || 0;
  }

  function renderPreview(payload) {
    var all = (payload.points || []);
    var points = filteredPoints().slice(0, PREVIEW_ROWS);
    $("preview-count").textContent = points.length;
    $("preview-total").textContent = all.length;
    $("edit-count").textContent = manualEdits(payload);
    renderTable($("preview-table"), [
      { key: "edit", label: "", edit: true },
      { key: "year", label: "Year" },
      { key: "title", label: "Title" },
      { key: "variant_family", label: "Variant" },
      { key: "price_lacs", label: "Price (lacs)" },
      { key: "expected_price_lacs", label: "FMV (lacs)", kind: "lacs" },
      { key: "residual_pkr", label: "Δ vs FMV (lacs)", kind: "signed-lacs" },
      { key: "deal_rating", label: "Deal", kind: "deal" },
      { key: "mileage_km", label: "Mileage (km)" },
      { key: "transmission", label: "Transmission" },
      { key: "location", label: "City" },
      { key: "posted_text", label: "Posted" },
      { key: "url", label: "Ad link", link: true }
    ], points);
  }

  function renderTable(table, columns, rows) {
    var head = "<thead><tr>" + columns.map(function (col) {
      return "<th>" + escapeHtml(col.label) + "</th>";
    }).join("") + "</tr></thead>";

    var body = (rows || []).map(function (row) {
      return "<tr>" + columns.map(function (col) {
        var value = row[col.key];
        if (col.edit) {
          return row.listing_id
            ? '<td><button type="button" class="btn btn-mini" data-edit-listing="' +
                escapeHtml(row.listing_id) + '">Edit</button></td>'
            : "<td>-</td>";
        }
        if (col.link) {
          return value
            ? '<td><a href="' + escapeHtml(value) + '" target="_blank" rel="noopener">open</a></td>'
            : "<td>-</td>";
        }
        if (col.kind === "deal") {
          var rating = value || "Unknown";
          return '<td><span class="deal-badge ' + dealBadgeClass(rating) + '">' +
            escapeHtml(rating) + "</span></td>";
        }
        if (col.kind === "lacs") {
          return "<td>" + escapeHtml(formatLacs(value)) + "</td>";
        }
        if (col.kind === "signed-lacs") {
          return "<td>" + escapeHtml(formatResidualLacs(value)) + "</td>";
        }
        if (value === null || value === undefined || value === "") { return "<td>-</td>"; }
        return "<td>" + escapeHtml(value) + "</td>";
      }).join("") + "</tr>";
    }).join("");

    table.innerHTML = head + "<tbody>" + body + "</tbody>";
  }

  // ------------------------------------------------------ manual corrections

  function filteredPoints() {
    var points = (state.payload && state.payload.points) || [];
    if (state.dealFilter) {
      points = points.filter(function (point) {
        return (point.deal_rating || "Unknown") === state.dealFilter;
      });
    }
    var needle = (state.previewFilter || "").trim().toLowerCase();
    if (!needle) { return points; }
    return points.filter(function (point) {
      return [point.listing_id, point.title, point.variant_family, point.variant,
        point.location, point.year, point.deal_rating].join(" ").toLowerCase()
        .indexOf(needle) !== -1;
    });
  }

  function findPoint(listingId) {
    var points = (state.payload && state.payload.points) || [];
    for (var i = 0; i < points.length; i += 1) {
      if (String(points[i].listing_id) === String(listingId)) { return points[i]; }
    }
    return null;
  }

  function openEditor(listingId) {
    var point = findPoint(listingId);
    if (!point) { showError("That listing is no longer part of this run."); return; }

    var specs = (state.payload && state.payload.editable_fields) || [];
    if (!specs.length) { showError("This run exposes no editable fields."); return; }

    var container = $("edit-fields");
    container.innerHTML = "";
    specs.forEach(function (spec) {
      var value = point[spec.key];
      var label = document.createElement("label");
      label.textContent = spec.label;

      var input;
      if (spec.type === "bool") {
        input = document.createElement("select");
        input.appendChild(new Option("No", "false"));
        input.appendChild(new Option("Yes", "true"));
        input.value = value ? "true" : "false";
      } else {
        input = document.createElement("input");
        input.type = spec.type === "int" ? "number" : "text";
        if (spec.type === "int") {
          input.step = "1";
          if (spec.min !== undefined) { input.min = spec.min; }
          if (spec.max !== undefined) { input.max = spec.max; }
          input.placeholder = spec.required ? "required" : "leave empty if unknown";
        }
        input.maxLength = 200;
        input.value = (value === null || value === undefined) ? "" : String(value);
      }

      input.id = "edit-" + spec.key;
      input.dataset.field = spec.key;
      input.dataset.initial = input.value;
      label.appendChild(input);
      container.appendChild(label);
    });

    state.editing = point;
    $("edit-listing-id").textContent = listingId;
    $("edit-snippet").textContent = [point.title, point.url].filter(Boolean).join("  \u00b7  ");
    $("edit-error").hidden = true;

    var dialog = $("edit-dialog");
    if (typeof dialog.showModal === "function") { dialog.showModal(); }
    else { dialog.setAttribute("open", ""); }
  }

  function closeEditor() {
    var dialog = $("edit-dialog");
    if (typeof dialog.close === "function" && dialog.open) { dialog.close(); }
    else { dialog.removeAttribute("open"); }
    state.editing = null;
  }

  function collectEdits() {
    var inputs = $("edit-fields").querySelectorAll("[data-field]");
    var fields = {};
    Array.prototype.forEach.call(inputs, function (input) {
      var value = input.value;
      if (value === input.dataset.initial) { return; }
      if (input.type === "number") {
        fields[input.dataset.field] = value.trim() === "" ? null : Number(value);
      } else {
        fields[input.dataset.field] = value;
      }
    });
    return fields;
  }

  function applyEditedPayload(data) {
    state.payload = data.payload;
    $("edit-count").textContent = data.manual_edits || 0;
    renderResultStats(data.payload, state.snapshot || {});
    renderPreview(data.payload);
    drawChart();
  }

  function submitEdits(event) {
    event.preventDefault();
    if (!state.editing) { return; }

    var error = $("edit-error");
    var fields = collectEdits();
    var names = Object.keys(fields);
    if (!names.length) {
      error.textContent = "Nothing changed yet — edit at least one field.";
      error.hidden = false;
      return;
    }

    var button = $("edit-save");
    var listingId = String(state.editing.listing_id);
    var jobId = state.jobId ||
      (state.payload && state.payload.meta && state.payload.meta.job_id);
    if (!jobId) {
      error.textContent = "This run is no longer available to edit.";
      error.hidden = false;
      return;
    }
    error.hidden = true;
    setBusy(button, true, "Saving…");

    postJSON("/api/listings/" + jobId + "/edit", {
      listing_id: listingId,
      fields: fields
    }).then(function (data) {
      applyEditedPayload(data);
      closeEditor();
      showInfo("Saved " + names.length + " correction" + (names.length === 1 ? "" : "s") +
        " (" + names.join(", ") + ") to listing " + listingId +
        ". The chart, the preview and every download were rebuilt from the corrected data.");
      $("results-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    }).catch(function (requestError) {
      error.textContent = requestError.message;
      error.hidden = false;
    }).then(function () {
      setBusy(button, false, "Save changes");
    });
  }

  function onPreviewClick(event) {
    var button = (event.target && event.target.closest)
      ? event.target.closest("[data-edit-listing]") : null;
    if (button) { openEditor(button.getAttribute("data-edit-listing")); }
  }

  function onFilterInput(event) {
    state.previewFilter = event.target.value;
    renderPreview(state.payload || {});
  }

  function onDealFilterChange(event) {
    state.dealFilter = event.target.value;
    renderPreview(state.payload || {});
  }

  function axisLabel(key) {
    var labels = {
      year: "Model year",
      price_lacs: "Price (lacs PKR)",
      price_pkr: "Price (PKR)",
      mileage_km: "Mileage (km)",
      engine_cc: "Engine (cc)"
    };
    return labels[key] || key;
  }

  function drawChart() {
    var chart = $("chart");
    if (!window.Plotly) {
      chart.innerHTML = '<p class="muted" style="padding:24px">Plotly.js could not be loaded ' +
        "(offline?). Use the standalone chart, CSV or JSON download instead.</p>";
      return;
    }
    var payload = state.payload;
    if (!payload || !(payload.points || []).length) {
      chart.innerHTML = '<p class="muted" style="padding:24px">No listings to plot.</p>';
      return;
    }

    var options = state.chartOptions;
    var points = payload.points.filter(function (point) {
      return (options.includeFeatured || !point.is_featured) &&
        point[options.x] !== null && point[options.x] !== undefined &&
        point[options.y] !== null && point[options.y] !== undefined;
    });
    if (!points.length) {
      chart.innerHTML = '<p class="muted" style="padding:24px">No points have both axes set.</p>';
      return;
    }

    var groups = new Map();
    points.forEach(function (point) {
      var key = point[options.color] || "Unknown";
      if (!groups.has(key)) { groups.set(key, []); }
      groups.get(key).push(point);
    });

    var entries = Array.from(groups.entries());
    if (options.color === "deal_rating") {
      entries.sort(function (a, b) {
        return DEAL_ORDER.indexOf(a[0]) - DEAL_ORDER.indexOf(b[0]);
      });
    }

    var traces = [];
    entries.forEach(function (entry) {
      var key = entry[0];
      var items = entry[1];
      var marker = {
        size: options.color === "deal_rating" ? 13 : 12,
        opacity: 0.85,
        line: { width: 1, color: "#ffffff" }
      };
      if (options.color === "deal_rating") { marker.color = dealColor(key); }
      traces.push({
        type: "scatter",
        mode: "markers",
        name: key,
        x: items.map(function (point) { return point[options.x]; }),
        y: items.map(function (point) { return point[options.y]; }),
        text: items.map(function (point) { return point.title || ""; }),
        customdata: items.map(function (point) {
          return [
            formatPkr(point.price_pkr), point.variant, point.mileage_km,
            point.transmission, point.fuel_type, point.location,
            point.posted_text, point.url, point.is_featured ? "featured" : "organic",
            formatLacs(point.expected_price_lacs),
            formatResidualLacs(point.residual_pkr),
            point.deal_rating || "Unknown",
            (point.z_score === null || point.z_score === undefined)
              ? "n/a" : Number(point.z_score).toFixed(2),
            point.fmv_method || "unavailable"
          ];
        }),
        marker: marker,
        hovertemplate: "<b>%{text}</b><br>" + axisLabel(options.x) + ": %{x}" +
          "<br>Price: %{customdata[0]}<br>Variant: %{customdata[1]}" +
          "<br>Mileage: %{customdata[2]} km<br>Transmission: %{customdata[3]}" +
          "<br>Fuel: %{customdata[4]}<br>City: %{customdata[5]}" +
          "<br>%{customdata[6]} (%{customdata[8]})" +
          "<br>FMV: %{customdata[9]}<br>Residual: %{customdata[10]}" +
          "<br>Deal: %{customdata[11]} (z=%{customdata[12]}, %{customdata[13]})" +
          "<extra>" + key + "</extra>"
      });
    });

    if (options.trend && payload.trend && options.x === "year" && options.y === "price_lacs") {
      traces.push({
        type: "scatter",
        mode: "lines",
        name: "Trend (OLS, R²=" + payload.trend.r_squared + ")",
        x: [payload.trend.x_min, payload.trend.x_max],
        y: [Math.max(0, payload.trend.y_at_x_min), Math.max(0, payload.trend.y_at_x_max)],
        line: { color: "#e2574c", width: 2, dash: "dash" },
        hovertemplate: "Trend: " + payload.trend.slope + " lacs per year<extra></extra>"
      });
    }

    var layout = {
      margin: { t: 24, r: 16, b: 70, l: 75 },
      xaxis: {
        title: axisLabel(options.x),
        dtick: options.x === "year" ? 1 : undefined,
        tickangle: -45,
        gridcolor: "#eef2f8",
        zeroline: false
      },
      yaxis: {
        title: axisLabel(options.y),
        type: options.logY ? "log" : "linear",
        gridcolor: "#eef2f8",
        zeroline: false
      },
      legend: { orientation: "h", y: -0.25 },
      hovermode: "closest",
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)"
    };

    Plotly.react(chart, traces, layout, {
      responsive: true,
      displaylogo: false,
      modeBarButtonsToRemove: ["lasso2d", "select2d"]
    });
  }

  // ------------------------------------------------------------------ wiring

  function wireEvents() {
    $("make").addEventListener("change", onMakeChange);
    $("model").addEventListener("change", function () { state.estimate = null; });
    $("make-custom").addEventListener("input", function () { state.estimate = null; });
    $("model-custom").addEventListener("input", function () { state.estimate = null; });

    $("btn-estimate").addEventListener("click", requestEstimate);
    $("btn-start").addEventListener("click", startScrape);
    $("btn-refresh-catalogue").addEventListener("click", function () { loadCatalogue(true); });

    $("btn-cancel").addEventListener("click", function () {
      if (!state.jobId) { return; }
      postJSON("/api/cancel/" + state.jobId, {}).then(function () {
        showInfo("Cancellation requested — the scraper stops after the current page.");
      }).catch(function (error) { showError(error.message); });
    });

    [["axis-x", "x"], ["axis-y", "y"], ["color-by", "color"]].forEach(function (pair) {
      $(pair[0]).addEventListener("change", function (event) {
        state.chartOptions[pair[1]] = event.target.value;
        drawChart();
      });
    });

    $("toggle-trend").addEventListener("change", function (event) {
      state.chartOptions.trend = event.target.checked; drawChart();
    });
    $("toggle-log-y").addEventListener("change", function (event) {
      state.chartOptions.logY = event.target.checked; drawChart();
    });
    $("toggle-featured").addEventListener("change", function (event) {
      state.chartOptions.includeFeatured = event.target.checked; drawChart();
    });

    $("preview-filter").addEventListener("input", onFilterInput);
    $("deal-filter").addEventListener("change", onDealFilterChange);
    $("preview-table").addEventListener("click", onPreviewClick);
    $("edit-form").addEventListener("submit", submitEdits);
    $("edit-cancel").addEventListener("click", function (event) {
      event.preventDefault();
      closeEditor();
    });
    $("edit-dialog").addEventListener("close", function () { state.editing = null; });
  }

  wireEvents();
  loadCatalogue(false);
})();


