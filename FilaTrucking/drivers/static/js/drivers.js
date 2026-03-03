document.addEventListener("DOMContentLoaded", function () {
  const addDocButton = document.getElementById("add_doc_button");
  const totalForms = document.getElementById(
    "id_driverdocument_set-TOTAL_FORMS",
  );
  const container = document.getElementById("document-form-container");
  const emptyForm = document.getElementById("empty-form");

  if (!addDocButton) {
    console.warn("add_doc_button not found on the page.");
    return;
  }

  addDocButton.addEventListener("click", function () {
    console.log("addDocButton clicked");
    const currentFormCount = parseInt(totalForms.value);

    const newForm = emptyForm.cloneNode(true);
    newForm.style.display = "block";
    newForm.setAttribute("id", "");

    const formRegex = new RegExp("__prefix__", "g");
    newForm.innerHTML = newForm.innerHTML.replace(formRegex, currentFormCount);

    container.appendChild(newForm);

    totalForms.value = currentFormCount + 1;
  });
});
