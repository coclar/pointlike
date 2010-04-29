"""
do pivot stuff 
$Header: /nfs/slac/g/glast/ground/cvs/pointlike/python/uw/thb_roi/pivot.py,v 1.2 2010/04/25 01:54:51 burnett Exp $

"""
version='$Revision: 1.2 $'.split()[1]
from uw.utilities import collection
from uw.thb_roi import pipeline, catalog
from skymaps import SkyDir
import numpy as np
import os, sys, exceptions

class InvalidParameter(exceptions.Exception):
    pass

def near(s, cat, tol=1.0):
    sdir = SkyDir(s.ra, s.dec)
    rtol = np.radians(tol)
    return [c for c in cat if sdir.difference(SkyDir(c.ra,c.dec))<rtol and c != s]

def selection(indir, select_all=False, mindist=0.25):
    r0 = pipeline.load_rec_from_pickles(indir)
     #--- 
    if select_all: return r0
    cut = (r0.band_ts>9)* (r0.qual<25) \
        * (r0.delta_ts<9) * (r0.pnorm>1e-15) * (r0.pindex>0.5) * (r0.pindex<4)
    r1 = r0[cut]
    print 'selected %d with standard cuts' %len(r1)
    if mindist==0: return r1
    # need to prune
    prunecut = catalog.prune(r1)
    print 'selected %d after prune within %.2f deg' % (prunecut.sum(), mindist)    
    return r1[prunecut]

def makepivot(indir, outdir, name, select_all=False, outfile='pivot.cxml', img_list=None, dzc='dzc.xml',
        href=None, linkname=None,
        origin = None,
        ):
    """
     indir: where to find the pickle files describing the sources, 
     outdir: where to setup the pivot files: expect to find a dzc file here with the names
     name:  name for the Pivot Collection
     select_all: if True, do not filter the sources
    """
    if not os.path.exists(outdir): 
        raise InvalidParameter('folder %s not found')
    fulloutfile = os.path.join(outdir, outfile)
    uwc = selection(indir, select_all)
    sdir = map(SkyDir, uwc.ra, uwc.dec)
    glon = np.asarray([s.l() for s in sdir])
    cglon = glon.copy()
    cglon[glon>180] = cglon[glon>180]-360
    col = collection.Collection(name, outdir,  uwc.name, dzc, img_list) 
    col.add_facet('ra',  'Number', 'F3', uwc.ra) 
    col.add_facet('dec', 'Number', 'F3', uwc.dec) 
    col.add_facet('glon', 'Number', 'F3', np.round(cglon,4)) 
    glat = np.asarray([s.b() for s in sdir])
    glat[np.abs(glat)<1e-3] = 0
    col.add_facet('glat', 'Number', 'F3',glat) 
    uwc.band_ts[uwc.band_ts<1]=0
    uwc.ts[uwc.ts<1]=0
    uwc.ts[uwc.ts>1e5]=1e5
    col.add_facet('band_ts', 'Number', 'F1', uwc.band_ts)
    col.add_facet('ts', 'Number', 'F1', uwc.ts)
    col.add_facet('isonorm', 'Number', 'F2', uwc.isonorm)
    uwc.galnorm[uwc.galnorm<1e-3]=0
    uwc.isonorm[uwc.isonorm<1e-3]=0
    col.add_facet('galnorm', 'Number', 'F2', uwc.galnorm)
    col.add_facet('a', 'Number', 'F3', uwc.a)
    col.add_facet('b', 'Number', 'F3', uwc.b)
    col.add_facet('qual', 'Number', 'F1', uwc.qual)
    col.add_facet('lognorm', 'Number', 'F3', np.log10(uwc.pnorm) )
    col.add_facet('pindex',  'Number', 'F3', uwc.pindex) 

    dts = uwc.delta_ts
    dts[dts<1e-3]=0
    col.add_facet('delta_ts', 'Number', 'F1', dts)
    col.add_facet('id_prob', 'Number', 'F3', uwc.id_prob)
    col.add_facet('class',  'String', 'C3', ['%3s'% s if s!='' else 'None' for s in uwc.aclass])
    if origin is not None:
        col.add_facet('origin', 'String', 'C', [ origin[n[:2]]     for n in uwc.name])
    if href is not None:
        hreflist = [href % n for n in uw.name[cut]]
        col.add_facet(linkname, 'Link', 'C', hreflist)
    
    # nearby sources
    nearlist = []
    nearmax=1.0
    nearcount = []
    for s in uwc:
        near_s = near(s, uwc, nearmax)
        nearlist.append( None if len(near_s)==0 else \
            (','.join([t.name for t in near_s]), '#'+'&amp;'.join(['$SEARCH$=FL.%s'%t.name for t in [s]+near_s])))
        nearcount.append(len(near_s))
    col.add_facet('Near', 'Link', 'C', nearlist)
    col.add_facet('nearby sources', 'Number', 'F', nearcount)
    
    print 'writing collection file to "%s" ...' % fulloutfile, 
    col.write(fulloutfile)
    print 'done!'


if __name__=='__main__':
    from optparse import OptionParser
    usage = """\n
usage: %prog [options] indir outdir name\n
Generate pivot collection
    indir: where to find the pickle files describing the sources
    outdir: where to setup the pivot files: expect to find a dzc file here with the names
    name:  name for the Pivot Collection (surround by double quotes if more than one token) """
    parser = OptionParser(usage, version=version)
    parser.add_option('-a', '--all', help='keep all sources (otherwise apply filter)', action='store_true', dest='select_all',default=False)
    parser.add_option( '--noorigin', help='do not check for standard origin from name', action='store_true', dest='noorigin', default=False)
    options, args = parser.parse_args()
    if len(args)!=3: 
        parser.print_usage()
        sys.exit(-1)
    origin = None if options.noorigin else {'1F':'1FGL', 'PG':'PGW', 'MR':'MRF', 'UW':'UW'}
    makepivot(args[0],args[1],args[2], select_all=options.select_all, origin=origin)
 
