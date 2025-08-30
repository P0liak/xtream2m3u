let categories = [];
let currentStep = 1;

async function loadCategories() {
  const url = document.getElementById("url").value.trim();
  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value.trim();
  const includeVod = document.getElementById("includeVod").checked;

  if (!url || !username || !password) {
    showError("Please fill in all required fields");
    return;
  }

  const loadingElement = document.getElementById("loading");
  const loadButton = document.getElementById("loadCategoriesText");

  loadButton.textContent = "Loading...";
  loadingElement.style.display = "block";
  hideAllSteps();
  clearResults();

  try {
    const params = new URLSearchParams({
      url: url,
      username: username,
      password: password,
    });

    if (includeVod) {
      params.append("include_vod", "true");
    }

    const response = await fetch(`/categories?${params}`);
    const data = await response.json();

    if (!response.ok) {
      throw new Error(
        data.details || data.error || "Failed to load categories"
      );
    }

    categories = data;
    displayCategoryChips(categories);
    showStep(2);
  } catch (error) {
    console.error("Error loading categories:", error);
    showError(`Failed to load categories: ${error.message}`);
    showStep(1);
  } finally {
    loadingElement.style.display = "none";
    loadButton.textContent = "Continue to Category Selection";
  }
}

function displayCategoryChips(categories) {
  const categoryChips = document.getElementById("categoryChips");
  categoryChips.innerHTML = "";

  // Group categories by content type
  const groupedCategories = {
    live: [],
    vod: [],
    series: [],
  };

  categories.forEach((category) => {
    const contentType = category.content_type || "live";
    if (groupedCategories[contentType]) {
      groupedCategories[contentType].push(category);
    }
  });

  // Define section headers and order
  const sections = [
    { key: "live", title: "📺 Live TV", icon: "📺" },
    { key: "vod", title: "🎬 Movies & VOD", icon: "🎬" },
    { key: "series", title: "📺 TV Shows & Series", icon: "📺" },
  ];

  sections.forEach((section) => {
    const sectionCategories = groupedCategories[section.key];
    if (sectionCategories && sectionCategories.length > 0) {
      // Create section header
      const sectionHeader = document.createElement("div");
      sectionHeader.className = "category-section-header";
      sectionHeader.innerHTML = `
                <h3>${section.title}</h3>
                <div class="section-header-actions">
                    <button class="btn-section-select-all" data-section="${section.key}">Select All</button>
                    <span class="category-count">${sectionCategories.length} categories</span>
                </div>
            `;
      categoryChips.appendChild(sectionHeader);

      // Create section container
      const sectionContainer = document.createElement("div");
      sectionContainer.className = "category-section";

      sectionCategories.forEach((category) => {
        const chip = document.createElement("div");
        chip.className = "category-chip";
        chip.dataset.categoryId = category.category_id;
        chip.dataset.categoryName = category.category_name;
        chip.dataset.contentType = category.content_type || "live";
        chip.onclick = () => toggleChip(chip);

        chip.innerHTML = `<span class="chip-text">${category.category_name}</span>`;
        sectionContainer.appendChild(chip);
      });

      categoryChips.appendChild(sectionContainer);
    }
  });

  // Add event listeners for section select all buttons
  document.querySelectorAll(".btn-section-select-all").forEach((button) => {
    button.addEventListener("click", (e) => {
      e.stopPropagation();
      const section = e.target.dataset.section;
      const sectionChips = document.querySelectorAll(
        `[data-content-type="${section}"]`
      );
      const allSelected = Array.from(sectionChips).every((chip) =>
        chip.classList.contains("selected")
      );

      // Toggle all chips in this section
      sectionChips.forEach((chip) => {
        if (allSelected) {
          chip.classList.remove("selected");
        } else {
          chip.classList.add("selected");
        }
      });

      // Update button text
      e.target.textContent = allSelected ? "Select All" : "Clear All";
      updateSelectionCounter();
    });
  });

  updateSelectionCounter();
}

function toggleChip(chip) {
  chip.classList.toggle("selected");
  updateSelectionCounter();
}

function updateSelectionCounter() {
  const selectedChips = document.querySelectorAll(".category-chip.selected");
  const selectedCount = selectedChips.length;
  const counter = document.getElementById("selectionCounter");
  const text = document.getElementById("selectionText");

  if (selectedCount === 0) {
    text.textContent =
      "Click categories to select them (or leave empty to include all)";
    counter.classList.remove("has-selection");
  } else {
    const filterMode = document.querySelector(
      'input[name="filterMode"]:checked'
    ).value;
    const action = filterMode === "include" ? "included" : "excluded";

    // Count by content type
    const contentTypeCounts = { live: 0, vod: 0, series: 0 };
    selectedChips.forEach((chip) => {
      const contentType = chip.dataset.contentType || "live";
      if (contentTypeCounts.hasOwnProperty(contentType)) {
        contentTypeCounts[contentType]++;
      }
    });

    // Build detailed text
    const parts = [];
    if (contentTypeCounts.live > 0)
      parts.push(`${contentTypeCounts.live} Live TV`);
    if (contentTypeCounts.vod > 0)
      parts.push(`${contentTypeCounts.vod} Movies/VOD`);
    if (contentTypeCounts.series > 0)
      parts.push(`${contentTypeCounts.series} TV Shows`);

    const breakdown = parts.length > 0 ? ` (${parts.join(", ")})` : "";
    text.textContent = `${selectedCount} categories will be ${action}${breakdown}`;
    counter.classList.add("has-selection");
  }
}

function showConfirmation() {
  const selectedCategories = getSelectedCategories();
  const filterMode = document.querySelector(
    'input[name="filterMode"]:checked'
  ).value;
  const includeVod = document.getElementById("includeVod").checked;
  const modal = document.getElementById("confirmationModal");
  const summary = document.getElementById("modalSummary");

  const url = document.getElementById("url").value.trim();
  const username = document.getElementById("username").value.trim();

  let categoryText;
  if (selectedCategories.length === 0) {
    categoryText = `All ${categories.length} categories`;
  } else {
    const action = filterMode === "include" ? "Include" : "Exclude";
    categoryText = `${action} ${selectedCategories.length} selected categories`;
  }

  const contentType = includeVod
    ? "Live TV + VOD/Movies/Shows"
    : "Live TV only";

  summary.innerHTML = `
        <div class="summary-row">
            <span class="summary-label">Service URL:</span>
            <span class="summary-value">${url}</span>
        </div>
        <div class="summary-row">
            <span class="summary-label">Username:</span>
            <span class="summary-value">${username}</span>
        </div>
        <div class="summary-row">
            <span class="summary-label">Content Type:</span>
            <span class="summary-value">${contentType}</span>
        </div>
        <div class="summary-row">
            <span class="summary-label">Filter Mode:</span>
            <span class="summary-value">${categoryText}</span>
        </div>
        <div class="summary-row">
            <span class="summary-label">Total Categories:</span>
            <span class="summary-value">${categories.length}</span>
        </div>
    `;

  modal.classList.add("active");
}

function closeModal() {
  document.getElementById("confirmationModal").classList.remove("active");
}

async function confirmGeneration() {
  closeModal();

  const url = document.getElementById("url").value.trim();
  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value.trim();
  const includeVod = document.getElementById("includeVod").checked;
  const selectedCategories = getSelectedCategories();
  const filterMode = document.querySelector(
    'input[name="filterMode"]:checked'
  ).value;

  hideAllSteps();
  document.getElementById("loading").style.display = "block";
  document.querySelector("#loading p").textContent =
    "Generating your playlist...";

  try {
    const params = new URLSearchParams({
      url: url,
      username: username,
      password: password,
      nostreamproxy: "true",
    });

    if (includeVod) {
      params.append("include_vod", "true");
    }

    if (selectedCategories.length > 0) {
      if (filterMode === "include") {
        params.append("wanted_groups", selectedCategories.join(","));
      } else {
        params.append("unwanted_groups", selectedCategories.join(","));
      }
    }

    const response = await fetch(`/m3u?${params}`);

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText || "Failed to generate M3U playlist");
    }

    const blob = await response.blob();
    const downloadUrl = window.URL.createObjectURL(blob);
    const downloadLink = document.getElementById("finalDownloadLink");
    downloadLink.href = downloadUrl;
    downloadLink.download = "playlist.m3u";
    downloadLink.style.display = "inline-flex";

    showStep(3);
  } catch (error) {
    console.error("Error generating M3U:", error);
    showError(`Failed to generate M3U: ${error.message}`);
    showStep(2);
  } finally {
    document.getElementById("loading").style.display = "none";
    document.querySelector("#loading p").textContent = "Loading categories...";
  }
}

function getSelectedCategories() {
  const selectedChips = document.querySelectorAll(".category-chip.selected");
  return Array.from(selectedChips).map((chip) => chip.dataset.categoryName);
}

function clearSelection() {
  const chips = document.querySelectorAll(".category-chip");
  chips.forEach((chip) => chip.classList.remove("selected"));

  // Reset section select all buttons
  const selectAllButtons = document.querySelectorAll(".btn-section-select-all");
  selectAllButtons.forEach((button) => {
    button.textContent = "Select All";
  });

  updateSelectionCounter();
}

// Flow management functions
function hideAllSteps() {
  document.querySelectorAll(".step").forEach((step) => {
    step.classList.remove("active");
  });
}

function showStep(stepNumber) {
  hideAllSteps();
  document.getElementById(`step${stepNumber}`).classList.add("active");
  currentStep = stepNumber;
}

function goBackToStep1() {
  showStep(1);
}

function startOver() {
  // Clear all form data
  document.getElementById("url").value = "";
  document.getElementById("username").value = "";
  document.getElementById("password").value = "";
  document.getElementById("includeVod").checked = false;

  // Reset categories and chips
  categories = [];
  document.getElementById("categoryChips").innerHTML = "";

  // Clear any download link
  const downloadLink = document.getElementById("finalDownloadLink");
  if (downloadLink.href && downloadLink.href.startsWith("blob:")) {
    URL.revokeObjectURL(downloadLink.href);
  }
  downloadLink.style.display = "none";

  clearResults();
  showStep(1);
}

function useOtherCredentials() {
  // Keep categories but clear credentials
  document.getElementById("url").value = "";
  document.getElementById("username").value = "";
  document.getElementById("password").value = "";

  clearResults();
  showStep(1);
}

function showError(message) {
  const resultsDiv = document.getElementById("results");
  resultsDiv.innerHTML = `<div class="alert alert-error">⚠️ ${message}</div>`;
}

function showSuccess(message) {
  const resultsDiv = document.getElementById("results");
  resultsDiv.innerHTML = `<div class="alert alert-success">✅ ${message}</div>`;
}

function clearResults() {
  document.getElementById("results").innerHTML = "";
}

// Trim input fields on blur to prevent extra spaces
function setupInputTrimming() {
  const textInputs = document.querySelectorAll(
    'input[type="text"], input[type="url"], input[type="password"]'
  );
  textInputs.forEach((input) => {
    input.addEventListener("blur", function () {
      this.value = this.value.trim();
    });
  });
}

// Initialize input trimming when page loads
document.addEventListener("DOMContentLoaded", setupInputTrimming);

// Update filter mode selection counter
document.addEventListener("change", function (e) {
  if (e.target.name === "filterMode") {
    updateSelectionCounter();
  }
});

// Modal click outside to close
document
  .getElementById("confirmationModal")
  .addEventListener("click", function (e) {
    if (e.target === this) {
      closeModal();
    }
  });

// Keyboard shortcuts
document.addEventListener("keydown", function (e) {
  // Escape to close modal
  if (e.key === "Escape") {
    closeModal();
    return;
  }

  if (e.ctrlKey || e.metaKey) {
    switch (e.key) {
      case "Enter":
        e.preventDefault();
        if (currentStep === 1) {
          loadCategories();
        } else if (currentStep === 2) {
          showConfirmation();
        }
        break;
      case "a":
        e.preventDefault();
        if (currentStep === 2) {
          const chips = document.querySelectorAll(".category-chip");
          const allSelected = Array.from(chips).every((chip) =>
            chip.classList.contains("selected")
          );
          chips.forEach((chip) => {
            if (allSelected) {
              chip.classList.remove("selected");
            } else {
              chip.classList.add("selected");
            }
          });
          updateSelectionCounter();
        }
        break;
    }
  }
});
