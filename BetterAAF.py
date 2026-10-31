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
    from betteraaf.playback import Engine
    eng = Engine()
    eng.load(path)
    blk = eng.render(eng.start, 1024, 1.0)
    print("player render peak:", float(abs(blk).max()), "tracks:", blk.shape[0])
    assert abs(blk).max() > 0, "player render produced silence"
    blk4 = eng.render(eng.start, 1024, 4.0)
    assert blk4.shape == blk.shape
    eng.close()
    try:
        import sounddevice as sd
        print("sounddevice", sd.__version__, "PortAudio", sd.get_portaudio_version()[1])
    except Exception as e:  # pas de carte son sur la machine de build : seul l'import compte
        if sys.platform == "darwin":
            raise
        print("sounddevice unavailable here:", e)
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
