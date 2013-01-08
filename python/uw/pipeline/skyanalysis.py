"""
Basic ROI analysis
$Header: /nfs/slac/g/glast/ground/cvs/pointlike/python/uw/pipeline/skyanalysis.py,v 1.24 2011/06/30 21:11:38 kerrm Exp $
"""
import os, pickle, glob, types
import numpy as np
from skymaps import SkyDir, PySkyFunction
from . import skymodel, dataspec
from uw.utilities import keyword_options, convolution
from uw.like import pointspec, roi_analysis, roi_managers, roi_diffuse, roi_localize
from uw.like import sed_plotter, tsmap_plotter, counts_plotter
from uw.like import roi_extended #ExtendedSource,ROIExtendedModel

# special function to replace or extend a docstring from that of another function
def decorate_with(other_func, append=False):
    def decorator(func):
        if append: func.__doc__ += other_func.__doc__ 
        else:      func.__doc__  = other_func.__doc__ 
        return func
    return decorator


        
class SkyAnalysis(pointspec.SpectralAnalysis):

    config = dict(
        fit_emin     = 100,
        fit_emax     = 562341.,
        minROI       = 5,
        maxROI       = 5,
        radius       =10, 
        irf          = 'P7SOURCE_V6',
        #fit_kw = dict(fit_bg_first = False, use_gradient = True, ),
        log          = None,
        convolve_kw = dict( resolution=0.125, # applied to OTF convolution: if zero, skip convolution
                        pixelsize=0.05, # ExtendedSourceConvolution
                        num_points=25), # AnalyticConvolution
        selector = skymodel.HEALPixSourceSelector, # this is a factory of SourceSelector objects
        )

    def __init__(self, sky, dataset, **kwargs):
        """
        sky:  skymodel object
        dataset: string, dict, or DataSpecification object
            if string: lookup a DataSpec in dataspec.DataSpec
            if dict: assume contains proper keys
        """
        # extract updates to local kw
        
        for kw in self.config:
            if kw in kwargs: self.config[kw]=kwargs.pop(kw)
        default_keys = [x[0] for x in self.defaults]
        # add keys to modify to kwargs
        for kw in default_keys: 
            if kw in self.config: kwargs[kw]= self.config[kw]
        month = kwargs.pop("month",None)
        super(SkyAnalysis,self).__init__( self._process_dataset(dataset,month=month), **kwargs)
        # now add what is left
        self.__dict__.update(self.config)
        self.skymodel = sky
        convolution.AnalyticConvolution.set_points(self.convolve_kw['num_points'])
        convolution.ExtendedSourceConvolution.set_pixelsize(self.convolve_kw['pixelsize'])
        if not self.quiet: 
            print >>self.log, self
            if self.log is not None: self.log.close()

    def _process_dataset(self,dataset,month):
        """ Parse the dataset as either a DataSpecification object, a dict, or a string lookup key.
            month: sub spec.
        """
        if hasattr(dataset,'binfile'): # dataset is DataSpecification instance
            return dataset
        if hasattr(dataset,'pop'): # dataset is a dict
            if 'data_name' not in dataset.keys():
                dataset['data_name'] = 'Custom Dataset %d'%id(dataset)
            dataspec.DataSpec.datasets[id(dataset)] = dataset
            return dataspec.DataSpec(id(dataset),month=month)
        # it is a string, check dictionary in ., then $FERMI/data
        for folder in  ('.',  os.path.join(os.path.expandvars('$FERMI'),'data')):
            dict_file=os.path.join(folder, 'dataspec.py')
            if os.path.exists(dict_file):
                try:
                    ldict = eval(open(dict_file).read())
                except:
                    print 'Data dictionary file %s not valid' % ldict
                    raise
                if dataset in ldict: 
                    print 'found dataset %s in $FERMI/data' % dataset
                    return dataspec.DataSpecification(folder, **ldict[dataset])
        # not found: this is deprecated, leave for backwards consisency
        return dataspec.DataSpec(dataset,month=month)
    
    def __str__(self):
        s = '%s configuration:\n'% self.__class__.__name__
        show = """CALDB irf skymodel dataspec fit_emin fit_emax fit_kw 
               minROI maxROI convolve_kw process_kw""".split()
        for key in show:
            s += '\t%-20s: %s\n' %(key,
                self.__dict__[key] if key in self.__dict__.keys() else 'not in self.__dict__!')
        return s

        s = 'SkyAnalysis analysis environment:\n'
        s += super(SkyAnalysis,self).__str__()
        return s
    
    def _diffuse_sources(self, src_sel):
        """ return a source manager for the diffuse,  global and extended sources
        """
        skydir = src_sel.skydir()
        # get all diffuse models appropriate for this ROI
        globals, extended = self.skymodel.get_diffuse_sources(src_sel)
       
        # perform OTF convolutions with PSFs: first diffuse, then extended if any
        # note that the wrapper could be roi_diffuse.ROIDiffuseModel_PC (for pre-convolved) it takes a tolerance
        # if resolution is zero, assume precompiled
        def otf_diffuse_mapper( source):
            res = self.convolve_kw['resolution']
            if res>0:
                return roi_diffuse.ROIDiffuseModel_OTF(self, source, skydir, pixelsize=res)
            return roi_diffuse.ROIDiffuseModel_PC(self, source, skydir)
        def diffuse_mapper(source): #pre-convolved if isotrop
            if source.name=='isotrop':
                return roi_diffuse.ROIDiffuseModel_PC(self, source, skydir)
            return otf_diffuse_mapper(source)
        global_models = map( diffuse_mapper, globals)

        def extended_mapper( source):
            return roi_extended.ROIExtendedModel.factory(self,source,skydir)
        extended_models = map(extended_mapper, extended)
        
        # create and return the manager
        return roi_managers.ROIDiffuseManager(global_models+extended_models, skydir, quiet=self.quiet)
        
    def _local_sources(self, src_sel):
        """ return a manager for the local sources with significant overlap with the ROI
        """
        ps = self.skymodel.get_point_sources(src_sel)
        skydir = src_sel.skydir()
        return roi_managers.ROIPointSourceManager(ps, skydir,quiet=self.quiet)
        
    def roi(self, *pars, **kwargs):
        """ return a roi_analysis.ROIAnalysis object based on the selector
        """
        roi_kw = kwargs.pop('roi_kw',None)
        src_sel = self.selector(*pars, **kwargs)

        ps_manager = self._local_sources(src_sel)
        bg_manager = self._diffuse_sources(src_sel)
        
        def iterable_check(x):
            return x if hasattr(x,'__iter__') else (x,x)


        r = PipelineROI(ps_manager.roi_dir, 
                    ps_manager, bg_manager, 
                    self, 
                    name = src_sel.name(), 
                    fit_emin=iterable_check(self.fit_emin), 
                    fit_emax=iterable_check(self.fit_emax),
                    quiet=self.quiet, 
                    #fit_kw = self.fit_kw,
                    roi_kw = roi_kw)
        return r

  
class PipelineROI(roi_analysis.ROIAnalysis):
    """ sub class of the standard ROIAnalysis class to cusomize the fit, add convenience functions
    """

    def __init__(self, *pars, **kwargs):
        self.fit_kw = kwargs.pop('fit_kw', dict())
        roi_kw = kwargs.pop('roi_kw',None)
        if roi_kw is not None: kwargs.update(roi_kw)
        self.likelihood_count=0
        self.prior = lambda x : 0 # default, no prior
        self.name = kwargs.pop('name', None)
        if self.name is None:
            if len(self.psm.point_sources)>0:
                self.name=self.psm.point_sources[0].name
            else: self.name='(not set)'
        self.center= pars[0] #roi_dir
        super(PipelineROI, self).__init__(*pars, **kwargs)
        
    def logLikelihood(self, parameters, *args):
        """ the total likelihood, according to model
            parameters parameters to pass to model
        """
        
        self.likelihood_count +=1
        if np.any(np.isnan(parameters)):
            # pretty ridiculous that this check must be made, but fitter passes NaNs...
            return 1e6
            # not sure if should "set parameters" in this case

        self.update_counts(parameters)

        ll = sum(band.logLikelihood() for band in self.bands)
        if np.isnan(ll) : return 1e6
        return ll -self.prior(self.psm.models)

    def fit(self, **kwargs):
        """ invoke base class fitter, but insert defaults first 
        """
        ignore_exception = kwargs.pop('ignore_exception', True)
        fit_kw = self.fit_kw
        fit_kw.update(kwargs)
        initial_count = self.likelihood_count
        initialL = self.logl
        try:
            super(PipelineROI, self).fit( **fit_kw)
        except Exception, msg:
            if not self.quiet: print 'Fit failed: %s' % msg
            if not ignore_exception: raise
        if not self.quiet:
            print 'logLikelihood called %d times, change: %.1f' % (self.likelihood_count - initial_count, initialL-self.logl )
        return self.logl
        
    @decorate_with(roi_analysis.ROIAnalysis.print_summary)
    def dump(self, sdir=None, galactic=False, maxdist=5, title=''):
        """ formatted table of sources positions and parameters in the ROI"""
        self.print_summary(sdir, galactic, maxdist, title)
    
    @decorate_with(sed_plotter.plot_sed)
    def plot_sed(self, **kwargs):
        return sed_plotter.plot_sed(self,**kwargs)

    @decorate_with(counts_plotter.plot_counts)
    def plot_counts(self, **kwargs):
        return counts_plotter.plot_counts(self, **kwargs)
    
    @decorate_with(tsmap_plotter.plot_tsmap)
    def plot_tsmap(self, *pars, **kwargs):
        return tsmap_plotter.plot_tsmap(self, *pars, **kwargs)
     
    def band_ts(self, which=0):
        """ return the sum of the individual band ts values
        """
        self.setup_energy_bands()
        ts = 0
        for eb in self.energy_bands:
            eb.bandFit(which)
            ts += eb.ts
        return ts

    def localize(self,which=0, tolerance=1e-3,update=False, verbose=False, bandfits=True, seedpos=None):
        """Localize a source using an elliptic approximation to the likelihood surface.

          which     -- index of point source; default to central 
                      **if localizing non-central, ensure ROI is large enough!**
          tolerance -- maximum difference in degrees between two successive best fit positions
          update    -- if True, update localization internally, i.e., recalculate point source contribution
          bandfits  -- if True, use a band-by-band (model independent) spectral fit; otherwise, use broabband fit
          seedpos   -- use for a modified position (pass to superclass)

         return fit position, change in TS
        """
        try:
            quiet, self.quiet = self.quiet, not verbose # turn off details of fitting
            loc, i, delta, deltaTS= super(PipelineROI,self).localize(which=which,bandfits=bandfits,
                            tolerance=tolerance,update=update,verbose=verbose, seedpos=seedpos)
            self.quiet = quiet
            if not self.quiet: 
                name = self.psm.point_sources[which].name if type(which)==types.IntType else which
                print 'Localization of %s: %d iterations, moved %.3f deg, deltaTS: %.1f' % \
                    (name,i, delta, deltaTS)
                self.print_ellipse()
        except Exception, e:
            print 'Localization failed! %s' % e
            self.qform=None
            loc, deltaTS = None, 99 
        #self.find_tsmax()
        return loc, deltaTS
    
  
    def tsmap(self, which=0, bandfits=True):
        """ return function of likelihood in neighborhood of given source
            tsm = roi.tsmap(which)
            size=0.25
            tsp = image.TSplot(tsm, center, size, pixelsize =size/20, axes=plt.gca())
            tsp.plot(center, label=name)
            tsp.show()
        """
        self.localizer = roi_localize.localizer(self, which, bandfits=bandfits)
        return PySkyFunction(self.localizer)
        
    def fit_ts_list(self, which=0):
        """ return breakdown of final fit ts per band """
        man,i = self.mapper(which)
        if 'energy_bands' not in self.__dict__:
            self.setup_energy_bands()
        if man != self.psm:
            # cannot handle extended sources
            return np.zeros((len(self.energy_bands)))

        self.zero_ps(which)
        self.update_counts(self.get_parameters())
        w0 = np.array([band.logLikelihood() for band in self.bands])
        self.unzero_ps(which)
        self.update_counts(self.get_parameters())
        w1 = np.array([band.logLikelihood() for band in self.bands])
        return 2*(w0-w1)
      
    def signal_counts(self, which):
        """ return list of (value, +sig, -sig) for measured counts per energy band
        (code cribbed from roi_bands.ROIEnergyBand, )
        
        """
        man,i = self.mapper(which)
        if 'energy_bands' not in self.__dict__:
            self.setup_energy_bands()
        r = []
        for eband in self.energy_bands:
            if man == self.psm:
                eband.bandFit(which=i)
            else:
                bfe=roi_extended.BandFitExtended(i,eband,self)
                bfe.fit()

            eband.m[0] = eband.uflux
            if man == self.psm:
                ul = sum( (b.phase_factor*b.expected(eband.m)*b.er[i] for b in eband.bands) )
            else:
                ul = sum( (b.phase_factor*b.expected(eband.m)*mb.er for b,mb in zip(bfe.bands,bfe.mybands)))

            if eband.flux is None:
                r.append([ 0, ul,0] )
            else:
                n = ul*eband.flux/eband.uflux
                r.append( [n,ul-n, n-ul*eband.lflux/eband.uflux] )
        return np.array(r, np.float32)
    