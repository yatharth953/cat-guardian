const field = document.getElementById("domains");
const status = document.getElementById("status");
const defaults = ["instagram.com"];

chrome.storage.local.get({ blockedDomains: defaults }).then(({ blockedDomains }) => {
  field.value = (blockedDomains || defaults).join("\n");
});

document.getElementById("save").addEventListener("click", async () => {
  const domains = [...new Set(field.value.split(/\r?\n/)
    .map(x => x.trim().toLowerCase().replace(/^https?:\/\//, "").split("/")[0].replace(/^\.+|\.+$/g, ""))
    .filter(x => x && !/\s/.test(x)))];
  await chrome.storage.local.set({ blockedDomains: domains });
  field.value = domains.join("\n");
  status.textContent = "Saved locally.";
});
