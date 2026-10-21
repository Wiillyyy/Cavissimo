const loader = document.getElementById("loader");
// Le navigateur distingue une actualisation d'une navigation ou d'un formulaire.
const navigation = performance.getEntriesByType("navigation")[0];

if (navigation && navigation.type === "reload") {
  setTimeout(() => {
    loader.classList.add("loader-hidden");
  }, 1400);
} else {
  loader.style.display = "none";
}

const navbar = document.getElementById("navbar");
window.addEventListener("scroll", () => {
  navbar.classList.toggle("scrolled", window.scrollY > 10);
});
