import sys


def selftest(path):
    """Headless analysis + export, used to check the built app."""
    import aaf2
    from betteraaf import model, media, analysis, writer
    with aaf2.open(path, "r") as f:
        comp = model.read_composition(f, model.list_compositions(f)[0])
        pool = media.MediaPool(f, path)
        r = analysis.analyze(comp, pool)
        from betteraaf import classify
        if classify.available():
            classify.classify_composition(list(comp.all_clips()), pool)
            cats = classify.track_categories(comp.tracks, [])
            print("content:", [(t.name, next(iter(c), None)) for t, c in cats])
            assert any(c for _t, c in cats), "content detection produced nothing"
        else:
            print("content detection: model not present")
        from betteraaf import levels
        lv = levels.measure(comp, pool)
        print("levels:", {k: round(v, 2) for k, v in lv.items() if isinstance(v, float)})
        assert lv and lv["peak"] > -60, "level measurement failed"
    print("exact", len(r.exact), "near", len(r.near), "dualmono", len(r.dualmono), "warnings", r.warnings)
    assert (len(r.exact), len(r.near), len(r.dualmono)) == (2, 2, 4), "unexpected results"
    plan = writer.Plan(exact_uids=[x.clip.uid for x in r.exact], near_uids=[x.clip.uid for x in r.near],
                       dm_uids=[x.clip.uid for x in r.dualmono])
    out = path[:-4] + "_selftest.aaf"
    print(writer.export(path, out, 0, r, plan, embed=True))
    print("SELFTEST OK")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--selftest":
        selftest(sys.argv[2])
    else:
        from betteraaf.gui import main
        main()
