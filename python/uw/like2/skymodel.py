"""
Manage the sky model for the UW all-sky pipeline
$Header: /nfs/slac/g/glast/ground/cvs/pointlike/python/uw/like2/Attic/skymodel.py,v 1.42 2013/10/14 15:11:42 burnett Exp $

"""
import os, pickle, glob, types, collections, zipfile
import pickle
import numpy as np
import pandas as pd
from skymaps import SkyDir, Band
from uw.utilities import keyword_options, makerec
from uw.like import Models, pointspec_helpers
from . import sources, diffusedict

class SkyModel(object):
    """
    Define a model of the gamma-ray sky, including point, extended, and global sources.
    Input is currently only from a folder containing all of the ROI pickles, in the format generated by the pipeline.
    Thus pipeline is completely iterative.
    
    Implement methods to create ROI for pointlike, used by pipeline.
    """
    
    defaults= (
        ('extended_catalog_name', None,  'name of folder with extended info\n'
                                         'if None, look it up in the config.txt file\n'
                                         'if "ignore", create model without extended sources'),
        #('diffuse', None,   'set of diffuse file names; if None, expect config to have'),
        ('auxcat', None, 'name of auxilliary catalog of point sources to append or names to remove',),
        ('newmodel', None, 'if not None, a string to eval\ndefault new model to apply to appended sources'),
        ('update_positions', None, 'set to minimum ts  update positions if localization information found in the database'),
        ('filter',   lambda s: True,   'source selection filter, applied when creating list of all soruces: see examples at the end. Can be string, which will be eval''ed '), 
        ('closeness_tolerance', 0., 'if>0, check each point source for being too close to another, print warning'),
        ('quiet',  True,  'suppress some messages if True' ),
        ('force_spatial_map', True, 'Force the use of a SpatialMap for extended sources'),
    )
    
    @keyword_options.decorate(defaults)
    def __init__(self, folder,  **kwargs):
        """
        folder: string
            name of folder to find all files defining the sky model, including:
             a subfolder 'pickle' with files *.pickle describing each ROI, partitioned as a HEALpix set. 
             -- OR: a file pickle.zip with the same. 
             a file 'config.txt', a python dictionary, which must start with '{', with possible entries for diffuse, extended 
        """
        keyword_options.process(self, kwargs)
        #if self.free_index is not None: 
        #    print (will free photon indices for ts>%d' % self.free_index)

        self.folder = os.path.expandvars(folder)
        if not os.path.exists(self.folder):
            raise Exception('sky model folder %s not found' % folder)
        self.get_config()
        self._setup_extended()
        self.diffuse_dict = diffusedict.DiffuseDict(folder)
        
        # evaluate the filter functions if necessary
        if type(self.filter)==types.StringType:
            self.filter = eval(self.filter)
        self._load_sources()
        self.load_auxcat()

    def __str__(self):
        return '%s.%s %s' %(self.__module__,self.__class__.__name__ ,os.path.join(os.getcwd(),self.folder))\
                +('\n\t\tdiffuse: %s' %self.diffuse_dict.keys())\
                +('\n\t\textended: %s' %self.extended_catalog_name )
     
    def __repr__(self): return self.__str__()
    
    def get_config(self, fn = 'config.txt'):
        """ parse the items in the configuration file into a dictionary
        """
        self.config={}
        fn = os.path.join(self.folder,fn)
        if not os.path.exists(fn): return
        txt = open(fn).read()
        if txt[0]=='{':
            # new format: just a dumped dict
            self.config = eval(txt)        
        # old format: more readable
        for line in txt:
            item = line.split(':')
            if len(item)>1:
                self.config[item[0].strip()]=item[1].strip()
 
    def load_auxcat(self, tol=0.2):
        """ modify the list of pointsources from entries in the auxcat: for now:
            * add it not there
            * move there, at new ra,dec
            * remove if ra<0
        
        """
        if self.auxcat is None or self.auxcat=='': 
            return
        cat = self.auxcat 
        if not os.path.exists(cat):
            cat = os.path.expandvars(os.path.join('$FERMI','catalog', cat))
        if not os.path.exists(cat):
            cat = os.path.join(self.folder, self.auxcat )
        if not os.path.exists(cat):
            raise Exception('auxilliary source catalog "%s" not found locally (%s) or in $FERMI/catalog'
                    %( self.auxcat, self.folder))
        ext = os.path.splitext(cat)[-1]            
        if ext =='.pickle':
            ss = pd.load(cat).itertuples()
            dataframe=True
            print ('loading auxcat from DataFrame')
        elif ext == '.csv':
            cc = pd.read_csv(cat)
            cols =list(cc.columns) 
            ss = cc.itertuples()
            i_eflux = cols.index('eflux')+1
            i_pindex= cols.index('pindex')+1
            i_e0 = cols.index('e0')+1
            dataframe=True
            print ('loading auxcat from csv')
        else:
            ss = makerec.load(cat); dataframe=False
        names = [s.name for s in self.point_sources]
        sdirs = [s.skydir for s in self.point_sources] 
        def check_near(sdir):
            closest = np.degrees(np.min(np.array(map(sdir.difference, sdirs))))
            return closest
        toremove=[]
        print ('process auxcat %s' %cat)
        for s in ss:
            if dataframe:
                # from csv: construct full model from parameters
                sname,sra,sdec = s[1:4]
                n0 = s[i_eflux] / (s[i_e0]**2*1e6)
                model = Models.LogParabola(p=[n0, s[i_pindex], 0., s[i_e0]])
                model.free[2:]=False
            else: # for comatibility: default from pater
                sname,sra,sdec = s.name, s.ra, s.dec
                model = self.newmodel
                if type(self.newmodel)==types.StringType: 
                    model = eval(self.newmodel)
                elif model is None: pass
                else:
                    model=self.newmodel.copy() # make sure to get a new object
            if not sname.startswith('SEED'): # allow underscores
                sname = sname.replace('_',' ') 
            if sname  not in names: 
                skydir=SkyDir(float(sra), float(sdec))
                close = check_near(skydir)
                if close < tol:
                    print ('\tsource %s close to another source, reject' % sname)
                    continue
                index=self.hpindex(skydir)
                if model is not None:
                    model.free[0] = True # must have at least one free parameter to be set up properly in an ROI
                self.point_sources.append(sources.PointSource(name=sname, skydir=skydir, index=index,  model=model))
                print ('\tadded new source %s at ROI %d (%.1f deg )' % (sname, index, close))
            else: 
                print ('\t source %s is in the model:' %sname,) # will remove if ra<0' % sname
                ps = self.point_sources[names.index(sname)]
                if float(sra)<=0: 
                    toremove.append(ps)
                    print (' removed.')
                else:
                    newskydir=SkyDir(float(sra),float(sdec))
                    print ('moved from %s to %s' % (ps.skydir, newskydir))
                    ps.skydir=newskydir
        for ps in toremove:
            self.point_sources.remove(ps)
            
    def _setup_extended(self):
        """ a little confusion: 'None' means that, but None means use the config file"""
        if self.extended_catalog_name is None:
            t=self.config.get('extended')
            if t[0]=='"' or t[0]=="'": t = eval(t)
            self.extended_catalog_name=t
        if not self.extended_catalog_name or self.extended_catalog_name=='None' or self.extended_catalog_name=='ignore':
            self.extended_catalog = None
            return
        extended_catalog_name = \
            os.path.expandvars(os.path.join('$FERMI','catalog',self.extended_catalog_name))
        if not os.path.exists(extended_catalog_name):
            raise Exception('extended source folder "%s" not found' % extended_catalog_name)
        self.extended_catalog= sources.ExtendedCatalog(extended_catalog_name, force_map=self.force_spatial_map)
        #print ('Loaded extended catalog %s' % self.extended_catalog_name)
        
    def _load_sources(self):
        """
        run through the pickled roi dictionaries, create lists of point and extended sources
        assume that the number of such corresponds to a HEALpix partition of the sky
        Note that if 'pickle.zip' exists, use it instead of a pickle folder
        """
        self.point_sources= []
        if os.path.exists(os.path.join(self.folder,'pickle.zip')):
            pzip = zipfile.ZipFile(os.path.join(self.folder,'pickle.zip'))
            files = ['pickle/HP12_%04d.pickle' %i for i in range(1728)]
            assert all(f in pzip.namelist() for f in files), 'Improper model zip file'
            opener = pzip.open
        else:
            files = glob.glob(os.path.join(self.folder, 'pickle', '*.pickle'))
            files.sort()
            opener = open
        self.nside = int(np.sqrt(len(files)/12))
        if len(files) != 12*self.nside**2:
            msg = 'Number of pickled ROI files, %d, found in folder %s, not consistent with HEALpix' \
                % (len(files),os.path.join(self.folder, 'pickle'))
            raise Exception(msg)
            
        ####self.global_sources = sources.GlobalSourceList()  # allocate list to index parameters for global sources
        self.extended_sources=[]  # list of unique extended sources
        self.changed=set() # to keep track of extended models that are different from catalog
        moved=0
        nfreed = 0
        self.tagged=set()
        source_names =[]
        for i,file in enumerate(files):
            p = pickle.load(opener(file))
            index = int(os.path.splitext(file)[0][-4:])
            assert i==index, 'logic error: file name %s inconsistent with expected index %d' % (file, i)
            roi_sources = p.get('sources',  {}) # don't know why this needed
            extended_names = {} if (self.__dict__.get('extended_catalog') is None) else self.extended_catalog.names
            for key,item in roi_sources.items():
                if key in extended_names: continue
                if key in source_names:
                    #if not self.quiet: print ('SkyModel warning: source with name %s in ROI %d duplicates previous entry: ignored'%(key, i))
                    continue
                source_names.append(key)
                skydir = item['skydir']
                if self.update_positions is not None:
                    ellipse = item.get('ellipse', None)
                    ts = item['ts']
                    if ellipse is not None and not np.any(np.isnan(ellipse)) :
                        fit_ra, fit_dec, a, b, ang, qual, delta_ts = ellipse
                        if qual<5 and a < 0.2 and \
                                ts>self.update_positions and delta_ts>0.1:
                            skydir = SkyDir(float(fit_ra),float(fit_dec))
                            moved +=1
                            self.tagged.add(i)
                
                ps = sources.PointSource(name=key,
                    skydir=skydir, model= sources.convert_model(item['model']),
                    ts=item['ts'],band_ts=item['band_ts'], index=index)
                if sources.validate(ps,self.nside, self.filter):
                    self._check_position(ps) # check that it is not coincident with previous source(warning for now?)
                    self.point_sources.append( ps)
            # make a list of extended sources used in the model   
            names = p.get('diffuse_names')
            for name, oldmodel in zip(names, p['diffuse']):
                model = sources.convert_model(oldmodel) # convert from old Model version if necessary 
                key = name.split('_')[0]
                if key in self.diffuse_dict:
                    self.diffuse_dict.add_model(index, name, model)
                elif  self.extended_catalog_name=='ignore': 
                    continue
                else:
                    try:
                        es = self.extended_catalog.lookup(name) if self.extended_catalog is not None else None
                    except Exception as msg:
                        print ('Skymodel: Failed to create model for %s' %name)
                        raise
                    if es is None:
                        #raise Exception( 'Extended source %s not found in extended catalog' %name)
                        print ('SkyModel warning: Extended source %s not found in extended catalog, removing' %name)
                        continue
                    if self.hpindex(es.skydir)!=index: continue
                    
                    if es.model.name!=model.name:
                        if name not in self.changed:
                            if not self.quiet: print ('SkyModel warning: catalog model  %s changed from %s for source %s: keeping change'%\
                                   (es.model.name, model.name, name))
                        self.changed.add(name)
                    es.smodel=es.model=model #update with current fit values always
                    if sources.validate(es,self.nside, self.filter): #lambda x: True): 
                        self.extended_sources.append(es)
        # check for new extended sources not yet in model
        self._check_for_extended()
        if self.update_positions and moved>0:
            print ('updated positions of %d sources, healpix ids in tagged' % moved)
 
    def _check_for_extended(self):
        if self.__dict__.get('extended_catalog') is None: return
        for i,name in enumerate(self.extended_catalog.names):
            if name.replace(' ','') not in [g.name.replace(' ','') for g in self.extended_sources]:
                es = self.extended_catalog.sources[i]
                print ('extended source %s [%d] added to model' % (name, self.hpindex(es.skydir)))
                t = self.extended_catalog.lookup(name)
                assert t is not None, 'logic error?'
                self.extended_sources.append(t)

    def _check_position(self, ps):
        if self.closeness_tolerance<0.: return
        tol = np.radians(self.closeness_tolerance)
        func = ps.skydir.difference
        for s in self.point_sources:
            delta=func(s.skydir)
            if delta<tol:
                print  ('SkyModel warning: appended source %s %.2f %.2f is %.2f deg (<%.2f) from %s (%d)'\
                    %(ps.name, ps.skydir.ra(), ps.skydir.dec(), np.degrees(delta), self.closeness_tolerance, s.name, s.index))
        
    def skydir(self, index):
        return Band(self.nside).dir(index)
    def hpindex(self, skydir):
        return Band(self.nside).index(skydir)
    
    def _select_and_freeze(self, sources, src_sel):
        """ 
        sources : list of Source objects
        src_sel : selection object
        -> list of selected sources selected by src_sel.include, 
            with some frozen according to src_sel.frozen
            order so the free are first
        """
        def copy_source(s): 
            return s.copy()
        inroi = filter(src_sel.include, sources)
        for s in inroi:
            #s.freeze(src_sel.frozen(s))
            s.model.free[:] = False if src_sel.frozen(s) else s.free[:]
        return map(copy_source, filter(src_sel.free,inroi)) + filter(src_sel.frozen, inroi)
    
    def get_point_sources(self, src_sel):
        """
        return a list of PointSource objects appropriate for the ROI
        """
        return self._select_and_freeze(self.point_sources, src_sel)

    def get_diffuse_sources(self, src_sel):
        """return diffuse, global and extended sources defined by src_sel
            always the global diffuse, and perhaps local extended sources.
            For the latter, make parameters free if not selected by src_sel.frozen
        """
        extended = self._select_and_freeze(self.extended_sources, src_sel)
        for s in extended: # this seems redundant, but was necessary
            s.model.free[:] = False if src_sel.frozen(s) else s.free[:]
            sources.validate(s,self.nside, None)
            s.smodel = s.model
            
        return self.get_global_sources(src_sel.skydir()), extended

    def get_global_sources(self, skydir):
        """ return global sources in ROI given by skydir
        """
        index = self.hpindex(skydir)
        return self.diffuse_dict.get_sources(index, sources.GlobalSource)
   
    def roi_rec(self, reload=False):
        self._load_recfiles(reload)
        return self.rois
    def source_rec(self, reload=False):
        self._load_recfiles(reload)
        return self.sources
    def find_source(self, name):
        """ return local source reference by name, or None """
        t = filter( lambda x: x.name==name, self.point_sources+self.extended_sources)
        return t[0] if len(t)==1 else None


class SourceSelector(object):
    """ Manage inclusion of sources in an ROI."""
    
    defaults = (
        ('max_radius',10,'Maximum radius (deg.) within which sources will be selected.'),
        ('free_radius',3,'Radius (deg.) in which sources will have free parameters'),
    )
    iteration =0
    @keyword_options.decorate(defaults)
    def __init__(self, skydir, **kwargs):
        self.mskydir = skydir
        keyword_options.process(self,kwargs)
        self.iteration = SourceSelector.iteration
        SourceSelector.iteration += 1
    
    def name(self):
        return 'ROI#%04d' % self.iteration

    def near(self,source, radius):
        return source.skydir.difference(self.mskydir)< np.radians(radius)

    def include(self,source):
        """ source -- an instance of Source """
        return self.near(source, self.max_radius)

    def free(self,source):
        """ source -- an instance of Source """
        return self.near(source, self.free_radius)

    def frozen(self,source): return not self.free(source)

    def skydir(self): return self.mskydir
        
class HEALPixSourceSelector(SourceSelector):
    """ Manage inclusion of sources in an ROI based on HEALPix.
    Overrides the free method to define HEALpix-based free regions
    """

    nside=12 # default, override externally
    @keyword_options.decorate(SourceSelector.defaults)
    def __init__(self, index, **kwargs):
        """ index : int
                HEALpix index for the ROI (RING)
        """
        keyword_options.process(self,kwargs)
        assert type(index)==types.IntType, 'Expect int type'
        self.myindex = index
        self.mskydir =  self.skydir(index)

    def __str__(self):
        return 'selector %s nside=%d, index=%d' %(self.__class__.__name__, self.nside, self.index)
        
    def name(self):
        return 'HP%02d_%04d' % (self.nside, self.myindex)

    def skydir(self, index=None):
        return Band(self.nside).dir(int(index)) if index is not None else self.mskydir
        
    def index(self, skydir):
        return Band(self.nside).index(skydir)
    
    def free(self,source):
        """
        source : instance of skymodel.Source
        -> bool, if this source in in the region where fit parameters are free
        """
        return self.index(source.skydir) == self.myindex
        
#========================================================================================
#  These classes are filters. An object of which can be loaded by the filter parameter
# A filter must implement a __call__ method, which must return True to keep the source.
# Since it is passed a reference to the source, it may change any characteristic, such as the model
#
# note MultiFilter that can be used to combine filters.

class RemoveByName(object):
    """ functor to remove sources, intended to be a filter for SkyModel"""
    def __init__(self, names):
        """ names : string or list of strings
            if a string, assume space-separated set of names (actually works for a single name)
        """
        tnames = names.split() if type(names)==types.StringType else names
        self.names = map( lambda x: x.replace('_', ' '), tnames)
    def __call__(self,ps):
        name = ps.name.strip().replace('_', ' ')
        return name not in self.names
    
class UpdatePulsarModel(object):
    """ special filter to replace models if necessary"""
    def __init__(self,  tol=0.25, ts_min=10, version=760, rename=True):
        import pyfits
        self.tol=tol
        self.ts_min=ts_min
        infile = os.path.expandvars(os.path.join('$FERMI','catalog','srcid', 'cat','obj-pulsar-lat_v%d.fits'%version)) 
        self.data = pyfits.open(infile)[1].data
        self.sdir = map(lambda x,y: SkyDir(float(x),float(y)), self.data.field('RAJ2000'), self.data.field('DEJ2000'))
        self.psr_names = self.data.field('Source_Name')
        self.tags = [False]*len(self.data)
        self.assoc = [['',-1, -1]]*len(self.data) #associated psr_names
        print ('Will check associations with LAT pulsar catalog %d' %version)
        self.rename = rename
        
    def get_pulsar_name(self, sdir):
        """ special to check for pulsar name"""
        for i,t in enumerate(self.sdir):
            dist = np.degrees(t.difference(sdir))
            if dist<self.tol:
                self.tags[i]=True
                return self.psr_names[i]
                break
        return None
        
    def __call__(self, s):
        sdir = s.skydir
        if hasattr(s, 'spatial_model') and s.spatial_model is not None:
            return True
        for i,t in enumerate(self.sdir):
            dist = np.degrees(t.difference(sdir))
            if dist<self.tol and s.ts>self.ts_min:
                self.tags[i]=True
                self.assoc[i]=(s.name, dist, s.ts)
                if self.rename and s.name != self.psr_names[i]: 
                    print ('Skymodel: renaming %s(%d) to %s' % (s.name, s.index, self.psr_names[i]))
                    s.name = self.psr_names[i]
                if s.model.name=='ExpCutoff': return True
                flux = s.model[0]
                if flux>1e-18:
                    print ('Skymodel: replacing model for: %s(%d): pulsar name: %s' % (s.name, s.index, self.psr_names[i]) )
                    s.model = Models.ExpCutoff()
                    s.free = s.model.free.copy()
                else:
                    print ('Apparent pulsar %s(%d), %s, is very weak, flux=%.2e <1e-13: leave as powerlaw' % (s.name, s.index, self.psr_names[i], flux))
                return True
        if s.model.name=='ExpCutoff':
            print ('Skymodel setup warning: %s (%d) not in LAT pulsar list, should not be expcutoff' % (s.name, s.index))
        return True
    def summary(self):
        n = len(self.tags)-sum(self.tags)
        if n==0: return
        print ('did not find %d sources ' % n)
        for i in range(len(self.tags)):
            if not self.tags[i]: print ('%s %9.3f %9.3f ' % (self.psr_names[i], self.sdir[i].ra(), self.sdir[i].dec()))
     
class MultiFilter(list):
    """ filter that is a list of filters """
    def __init__(self, filters):
        """ filters : list
                if an element of the list is a string, evaluate it first
        """
        for filter in filters: 
            if type(filter)==types.StringType:
                filter = eval(filter)
            self.append(filter)
            
                
    def __call__(self, source):
        for filter in self:
            if not filter(source): return False
        return True

class FluxFreeOnly(object):
    """ Filter that fixes all but flux for sources, and freezes diffuse """
    def __init__(self):
        pass
    def __call__(self, source):
        if np.any(source.free) :
            source.free[1:]=False
        return True
  
class FreeIndex(object):
    """ make sure all spectral indices are free"""
    def __init__(self):
        pass
    def __call__(self, ps):
        model = ps.model
        if ps.model.name=='LogParabola':
            ps.free[1]=True
        return True
class AllFixed(object):
    """ useful for setting all Global parameters fixed"""
    def __call__(self, s):
        s.model.free[:] = False
class GlobalCheck(object):
    """ default check for global """
    def __call__(self, s):
        if s.name=='limb': # kluge for now.
            pars = s.model.get_parameters()
            if pars[1]<-2: 
                pars[:]=[-2, -1]
                s.model.set_parameters(pars)
class LimbSpecial(object):
    """ fix limb to freeze Front """
    def __call__(self, s):
        if s.name=='limb':
            print ('fixing limb')
            s.model.free = np.array([False, True])
            pars = s.model.get_parameters()
            if pars[0]<=-3: s.model.set_parameters(np.array([-1.]))
class TScut(object):
    def __init__(self, cut=10):
        self.cut=cut
    def __call__(self, s):
        return s.ts>10 if hasattr(s,'ts') else True
        
class Rename(object):
    """ filter to rename sources
    """
    def __init__(self, namefile):
        """ namefile : string
                text file with from to pairs; if second is '*', delete the source
        """
        def parse_line(line):
            t = line.split()
            if len(t)==2: return t
            return (t[0]+'_'+t[1], t[2])
        with open(namefile) as inp:
            self.namedict = dict( parse_line(line) for line in inp if len(line)>9)
        print ('found %d names to convert' % len(self.namedict))
        
    def __call__(self, s):
        t = s.name
        s.name = self.namedict.get(s.name, s.name)
        return s.name != '*'
        if s.name[0] =='*':
           print ('deleting', t)
           return False
        return True
