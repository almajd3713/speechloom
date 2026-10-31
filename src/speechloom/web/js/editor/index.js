// Mount point for the transcript editor (filled in by the editor work package).
// The job page calls mount(container, { api, jobId, result, resultCount }) and keeps the returned
// function to call when the page is left.

export function mount() {
  return () => {};
}
