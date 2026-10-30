import sys


def selftest(path):
    """Analyse + export sans interface (utilisé pour vérifier l'appli compilée)."""
    import aaf2
    from betteraaf import model, media, analysis, writer
    with aaf2.open(path, "r") as f:
        comp = model.read_composition(f, model.list_compositions(f)[0])
        pool = media.MediaPool(f, path)
        r = analysis.analyze(comp, pool)
    print("exact", len(r.exact), "near", len(r.near), "dualmono", len(r.dualmono), "warnings", r.warnings)
    assert (len(r.exact), len(r.near), len(r.dualmono)) == (2, 2, 4), "résultats inattendus"
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
