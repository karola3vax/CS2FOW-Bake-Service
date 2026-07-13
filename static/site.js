(() => {
  const desktopMotion = window.matchMedia("(min-width: 1024px) and (prefers-reduced-motion: no-preference)");
  if (!desktopMotion.matches || !document.body.classList.contains("home-page")) return;

  const hero = document.querySelector("[data-hero]");
  const reveals = [...document.querySelectorAll("[data-reveal]")];
  if (!hero || typeof IntersectionObserver === "undefined") return;

  document.documentElement.classList.add("motion-active");

  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entry.target.classList.add("is-visible");
      observer.unobserve(entry.target);
    }
  }, { threshold: 0.16, rootMargin: "0px 0px -8%" });
  reveals.forEach((element) => observer.observe(element));

  let queued = false;
  const updateHero = () => {
    queued = false;
    const height = Math.max(hero.offsetHeight * 0.9, 1);
    const progress = Math.min(Math.max(window.scrollY / height, 0), 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    hero.style.setProperty("--mesh-y", `${72 - eased * 192}px`);
    hero.style.setProperty("--mesh-scale", `${0.86 + eased * 0.2}`);
    hero.style.setProperty("--halo-y", `${eased * -120}px`);
    hero.style.setProperty("--copy-y", `${eased * -56}px`);
    hero.style.setProperty("--copy-opacity", `${1 - eased * 0.58}`);
  };

  const requestUpdate = () => {
    if (queued) return;
    queued = true;
    window.requestAnimationFrame(updateHero);
  };

  updateHero();
  window.addEventListener("scroll", requestUpdate, { passive: true });
  window.addEventListener("resize", requestUpdate, { passive: true });
})();
