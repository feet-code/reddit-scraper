// Pure DOM reader. No API calls, cookies, app state, or login-form values.
({detail = false, thread_id = ""} = {}) => {
  const text = el => (el?.innerText || "").replace(/\s+/g, " ").trim();
  const visible = el => !!el && el.getClientRects().length > 0;
  const attr = (el, ...names) => names.map(n => el.getAttribute(n)).find(v => v !== null && v !== "") || "";
  const enabled = (el, name) => el.hasAttribute(name) && el.getAttribute(name) !== "false";
  const removed = value => /^\[(?:deleted|removed)(?: by user)?\]$/i.test(value.trim());
  const parseLink = href => {
    try {
      const url = new URL(href, location.origin);
      if (!["reddit.com", "www.reddit.com"].includes(url.hostname)) return null;
      const match = url.pathname.match(/^\/r\/([\w]+)\/comments\/([a-z0-9]+)\//i);
      if (!match) return null;
      return {subreddit: match[1], thread_id: "t3_" + match[2], permalink: url.origin + url.pathname};
    } catch { return null; }
  };
  const posts = new Map(), comments = new Map(), removed_ids = new Set();
  for (const el of document.querySelectorAll("shreddit-post")) {
    if (!visible(el) || enabled(el, "promoted") || enabled(el, "is-promoted") || enabled(el, "nsfw")) continue;
    const link = parseLink(attr(el, "permalink", "content-href"));
    if (!link || (detail && thread_id && link.thread_id !== thread_id)) continue;
    const title = attr(el, "post-title") || text(el.querySelector("h1,h2,h3"));
    const body = text(el.querySelector('[slot="text-body"]'));
    const id = attr(el, "id", "post-id") || link.thread_id;
    if (removed(title) || removed(body) || /^(DELETED|REMOVED)$/i.test(attr(el, "item-state"))) {
      removed_ids.add(id); continue;
    }
    if (!title) continue;
    posts.set(id, {id, ...link, kind:"post", title, body,
      created_utc: attr(el, "created-timestamp") || el.querySelector("time")?.getAttribute("datetime"),
      score: attr(el, "score"), num_comments: attr(el, "comment-count"), complete:detail});
  }
  // Search results sometimes use an article/card instead of shreddit-post.
  if (!detail) {
    for (const anchor of document.querySelectorAll('main a[href*="/comments/"]')) {
      if (!visible(anchor) || anchor.closest("shreddit-post")) continue;
      const link = parseLink(anchor.getAttribute("href"));
      if (!link || posts.has(link.thread_id)) continue;
      const card = anchor.closest("article,search-telemetry-tracker,[data-testid='search-post']");
      const heading = card?.querySelector("h2,h3,[data-testid='post-title']");
      const title = heading ? text(heading) : (anchor.matches("h2 a,h3 a") ? text(anchor) : "");
      if (title.length < 8 || /^(promoted|advertisement):/i.test(title)) continue;
      posts.set(link.thread_id, {id:link.thread_id, ...link, kind:"post", title, body:"",
        created_utc:card?.querySelector("time")?.getAttribute("datetime"), complete:false});
    }
  }
  if (detail) {
    const rootPost = posts.get(thread_id) || Array.from(posts.values())[0];
    for (const el of document.querySelectorAll("shreddit-comment")) {
      if (!visible(el)) continue;
      // Reading the author attribute only to exclude AutoModerator; never return it.
      if (/^automoderator$/i.test(attr(el, "author"))) continue;
      const own = selector => Array.from(el.querySelectorAll(selector)).find(n => n.closest("shreddit-comment") === el);
      const body = text(own('[slot="comment"]'));
      const id = attr(el, "thingid", "id");
      if (!/^t1_[a-z0-9]+$/i.test(id)) continue;
      if (removed(body)) { removed_ids.add(id); continue; }
      if (!body) continue;
      const link = parseLink(attr(el, "permalink"));
      if (!link || (thread_id && link.thread_id !== thread_id)) continue;
      comments.set(id, {id, ...link, kind:"comment", title:rootPost?.title || "", body,
        created_utc:own("time")?.getAttribute("datetime"), score:attr(el, "score"), complete:true});
    }
  }
  const bodyText = text(document.body);
  const outsideHeadings = Array.from(document.querySelectorAll("h1,h2,[role='dialog']"))
    .filter(e => visible(e) && !e.closest("shreddit-post,shreddit-comment,article"))
    .map(text).join(" ");
  const screen = posts.size ? outsideHeadings : bodyText;
  let block = "";
  if (/verify (?:that )?you(?:'re| are) (?:a )?human|checking your browser|blocked by network security|automated (?:queries|traffic)|whoa there, pardner|unusual traffic|security verification/i.test(screen)) block = "Reddit is showing a security/verification screen";
  else if (/^\/(login|register)\/?/.test(location.pathname) || /log in to (?:continue|view)|sign in to (?:continue|view)/i.test(screen)) block = "Reddit requires a manual sign-in";
  else if (!posts.size && /private community|banned community|community has been banned|mature content|confirm your age/i.test(screen)) block = "This community is unavailable or requires a manual access check";
  const empty = !block && !posts.size && /(?:no results found|couldn.t find any results|there are no posts|this community doesn.t have any posts|no posts yet)/i.test(bodyText);
  return {posts:Array.from(posts.values()), comments:Array.from(comments.values()),
    removed_ids:Array.from(removed_ids), block, empty, title:document.title};
}
