# Copyright (c) 2012-2016 by the GalSim developers team on GitHub
# https://github.com/GalSim-developers
#
# This file is part of GalSim: The modular galaxy image simulation toolkit.
# https://github.com/GalSim-developers/GalSim
#
# GalSim is free software: redistribution and use in source and binary forms,
# with or without modification, are permitted provided that the following
# conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions, and the disclaimer given in the accompanying LICENSE
#    file.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions, and the disclaimer given in the documentation
#    and/or other materials provided with the distribution.
#
"""@file chromatic.py
Define wavelength-dependent surface brightness profiles.

Implementation is done by constructing GSObjects as functions of wavelength. The drawImage()
method then integrates over wavelength while also multiplying by a throughput function.

Possible uses include galaxies with color gradients, automatically drawing a given galaxy through
different filters, or implementing wavelength-dependent point spread functions.
"""

import numpy as np

import galsim

class ChromaticObject(object):
    """Base class for defining wavelength-dependent objects.

    This class primarily serves as the base class for chromatic subclasses, including Chromatic,
    ChromaticSum, and ChromaticConvolution.  See the docstrings for these classes for more details.

    Initialization
    --------------

    A ChromaticObject can also be instantiated directly from an existing GSObject.
    In this case, the newly created ChromaticObject will act nearly the same way the original
    GSObject works, except that it has access to the ChromaticObject methods described below;
    especially expand(), dilate() and shift().

    @param gsobj  The GSObject to be chromaticized.

    Methods
    -------

    gsobj = chrom_obj.evaluateAtWavelength(lambda) returns the monochromatic surface brightness
    profile (as a GSObject) at a given wavelength (in nanometers).

    The interpolate() method can be used for non-separable ChromaticObjects to expedite the
    image rendering process.  See the docstring of that method for more details and discussion of
    when this is a useful tool (and the interplay between interpolation, object
    transformations, and convolutions).

    Also, ChromaticObject has most of the same methods as GSObjects with the following exceptions:

    The GSObject access methods (e.g. xValue(), maxK(), etc.) are not available.  Instead,
    you would need to evaluate the profile at a particular wavelength and access what you want
    from that.

    There is no withFlux() method, since this is in general undefined for a chromatic object.
    See the SED class for how to set a chromatic flux density function.

    The transformation methods: transform(), expand(), dilate(), magnify(), shear(), rotate(),
    lens(), and shift() can now accept functions of wavelength as arguments, as opposed to the
    constants that GSObjects are limited to.  These methods can be used to effect a variety of
    physical chromatic effects, such as differential chromatic refraction, chromatic seeing, and
    diffraction-limited wavelength-dependence.

    The drawImage() method draws the object as observed through a particular bandpass, so the
    function parameters are somewhat different.  See the docstring for ChromaticObject.drawImage()
    for more details.

    The drawKImage() method is not yet implemented.
    """

    # In general, `ChromaticObject` and subclasses should provide the following interface:
    # 1) Define an `evaluateAtWavelength` method, which returns a GSObject representing the
    #    profile at a particular wavelength.
    # 2) Define a `withScaledFlux` method, which scales the flux at all wavelengths by a fixed
    #    multiplier.
    # 3) Potentially define their own `__repr__` and `__str__` methods.  Note that the default
    #    assumes that `.obj` is the only attribute of significance, but this isn't always
    #    appropriate, (e.g. ChromaticSum, ChromaticConvolution).
    # 4) Initialize a `separable` attribute.  This marks whether (`separable = True`) or not
    #    (`separable = False`) the given chromatic profile can be factored into a spatial profile
    #    and a spectral profile.  Separable profiles can be drawn quickly by evaluating at a single
    #    wavelength and adjusting the flux via a (fast) 1D integral over the spectral component.
    #    Inseparable profiles, on the other hand, need to be evaluated at multiple wavelengths
    #    in order to draw (slow).
    # 5) Separable objects must initialize an `SED` attribute, which is a callable object (often a
    #    `galsim.SED` instance) that returns the _relative_ flux of the profile at a given
    #    wavelength. (The _absolute_ flux is controlled by both the `SED` and the `.flux` attribute
    #    of the underlying chromaticized GSObject(s).  See `galsim.Chromatic` docstring for details
    #    concerning normalization.)
    # 6) Initialize a `wave_list` attribute, which specifies wavelengths at which the profile (or
    #    the SED in the case of separable profiles) will be evaluated when drawing a
    #    ChromaticObject.  The type of `wave_list` should be a numpy array, and may be empty, in
    #    which case either the Bandpass object being drawn against, or the integrator being used
    #    will determine at which wavelengths to evaluate.

    # Additionally, instances of `ChromaticObject` and subclasses will usually have either an `obj`
    # attribute representing a manipulated `GSObject` or `ChromaticObject`, or an `objlist`
    # attribute in the case of compound classes like `ChromaticSum` and `ChromaticConvolution`.


    def __init__(self, obj):
        self.separable = obj.separable
        self.interpolated = obj.interpolated
        self.wave_list = obj.wave_list
        if isinstance(obj, galsim.GSObject):
            # The following might be contraversial, but I'm (JM) declaring that the most common use
            # case for calling the galsim.ChromaticObject constructor on a GSObject is for setting
            # up a wavelength dependent PSF by chromatically transforming said GSObject.  In that
            # case, we want self.SED to be None.  The exception is if the GSObject has non-unit
            # flux, which doesn't make sense for a PSF.  In that case, we initialize self.SED to the
            # appropriate constant (in fphotons) flux.
            if obj.flux == 1.0:
                self.SED = None
                self.obj = obj
                self._norm = 1.0
            else:
                self.SED = galsim.SED(str(obj.flux), 'nm', 'fphotons')
                self.obj = obj/obj.flux
                self._norm = None
        elif isinstance(obj, ChromaticObject):
            self.obj = obj
            self.SED = obj.SED
            self._norm = obj._norm
        else:
            raise TypeError("Can only directly instantiate ChromaticObject with a GSObject "
                            "or ChromaticObject argument.")

    @staticmethod
    def _get_multiplier(sed, bandpass, wave_list):
        wave_list = np.array(wave_list)
        if len(wave_list) > 0:
            multiplier = np.trapz(sed(wave_list) * bandpass(wave_list), wave_list)
        else:
            multiplier = galsim.integ.int1d(lambda w: sed(w) * bandpass(w),
                                            bandpass.blue_limit, bandpass.red_limit)
        return multiplier

    @staticmethod
    def resize_multiplier_cache(maxsize):
        """ Resize the cache (default size=10) containing the integral over the product of an SED
        and a Bandpass, which is used by ChromaticObject.drawImage().

        @param maxsize  The new number of products to cache.
        """
        ChromaticObject._multiplier_cache.resize(maxsize)

    def _fiducial_profile(self, bandpass):
        """
        Return a fiducial achromatic profile of a chromatic object that can be used to estimate
        default output image characteristics, or in the case of separable profiles, can be scaled to
        give the monochromatic profile at any wavelength or the wavelength-integrated profile.
        """
        bpwave = bandpass.effective_wavelength
        prof0 = self.evaluateAtWavelength(bpwave)
        if prof0.flux != 0:
            return bpwave, prof0

        candidate_waves = np.concatenate(
            [np.array([0.5 * (bandpass.blue_limit + bandpass.red_limit)]),
             bandpass.wave_list,
             self.wave_list])
        # Prioritize wavelengths near the bandpass effective wavelength.
        candidate_waves = candidate_waves[np.argsort(np.abs(candidate_waves - bpwave))]
        for w in candidate_waves:
            prof0 = self.evaluateAtWavelength(w)
            if prof0.flux != 0:
                return w, prof0

        raise ValueError("Could not locate fiducial wavelength where SED * Bandpass is nonzero.")

    def __eq__(self, other):
        return (isinstance(other, galsim.ChromaticObject) and
                self.obj == other.obj)

    def __ne__(self, other): return not self.__eq__(other)

    def __hash__(self): return hash(("galsim.ChromaticObject", self.obj))

    def __repr__(self):
        return 'galsim.ChromaticObject(%r)'%self.obj

    def __str__(self):
        return 'galsim.ChromaticObject(%s)'%self.obj

    def interpolate(self, waves, oversample_fac=1.):
        """
        This method is used as a pre-processing step that can expedite image rendering using objects
        that have to be built up as sums of GSObjects with different parameters at each wavelength,
        by interpolating between Images at each wavelength instead of making a more costly
        instantiation of the relevant GSObject at each value of wavelength at which the bandpass is
        defined.  This routine does a costly initialization process to build up a grid of images to
        be used for the interpolation later on.  However, the object can get reused with different
        bandpasses, so there should not be any need to make many versions of this object, and there
        is a significant savings each time it is drawn into an image.  As a general rule of thumb,
        chromatic objects that are separable do not benefit from this particular optimization,
        whereas those that involve making GSObjects with wavelength-dependent keywords or
        transformations do benefit from it.  Note that the interpolation scheme is simple linear
        interpolation in wavelength, and no extrapolation beyond the originally-provided range of
        wavelengths is permitted.

        The speedup involved in using interpolation depends in part on the bandpass used for
        rendering (since that determines how many full profile evaluations are involved in rendering
        the image).  However, for ChromaticAtmosphere with simple profiles like Kolmogorov, the
        speedup in some simple example cases is roughly a factor of three, whereas for more
        expensive to render profiles like the ChromaticOpticalPSF, the speedup is more typically a
        factor of 10-50.

        Achromatic transformations can be applied either before or after setting up interpolation,
        with the best option depending on the application.  For example, when rendering many times
        with the same achromatic transformation applied, it is typically advantageous to apply the
        transformation before setting up the interpolation.  But there is no value in this when
        applying different achromatic transformation to each object.  Chromatic transformations
        should be applied before setting up interpolation; attempts to render images of
        ChromaticObjects with interpolation followed by a chromatic transformation will result in
        the interpolation being unset and the full calculation being done.

        Because of the clever way that the ChromaticConvolution routine works, convolutions of
        separable chromatic objects with non-separable ones that use interpolation will still
        benefit from these optimizations.  For example, a non-separable chromatic PSF that uses
        interpolation, when convolved with a sum of two separable galaxy components each with their
        own SED, will be able to take advantage of this optimization.  In contrast, when convolving
        two non-separable profiles that already have interpolation set up, there is no way to take
        advantage of that interpolation optimization, so it will be ignored and the full calculation
        will be done.  However, interpolation can be set up for the convolution of two non-separable
        profiles, after the convolution step.  This could be beneficial for example when convolving
        a chromatic optical PSF and chromatic atmosphere, before convolving with multiple galaxy
        profiles.

        For use cases requiring a high level of precision, we recommend a comparison between the
        interpolated and the more accurate calculation for at least one case, to ensure that the
        required precision has been reached.

        The input parameter `waves` determines the input grid on which images are precomputed.  It
        is difficult to give completely general guidance as to how many wavelengths to choose or how
        they should be spaced; some experimentation compared with the exact calculation is warranted
        for each particular application.  The best choice of settings might depend on how strongly
        the parameters of the object depend on wavelength.

        @param waves            The list, tuple, or NumPy array of wavelengths to be used when
                                building up the grid of images for interpolation.  The wavelengths
                                should be given in nanometers, and they should span the full range
                                of wavelengths covered by any bandpass to be used for drawing Images
                                (i.e., this class will not extrapolate beyond the given range of
                                wavelengths).  They can be spaced any way the user likes, not
                                necessarily linearly, though interpolation will be linear in
                                wavelength between the specified wavelengths.
        @param oversample_fac   Factor by which to oversample the stored profiles compared to the
                                default, which is to sample them at the Nyquist frequency for
                                whichever wavelength has the highest Nyquist frequency.
                                `oversample_fac`>1 results in higher accuracy but costlier
                                pre-computations (more memory and time). [default: 1]

        @returns the version of the Chromatic object that uses interpolation
                 (This will be an InterpolatedChromaticObject instance.)
        """
        return InterpolatedChromaticObject(self, waves, oversample_fac)

    @property
    def deinterpolated(self):
        """Version of object with any interpolation from InterpolatedChromaticObject reverted.
        """
        return self._deinterpolate()

    def _deinterpolate(self):
        return self.obj._deinterpolate()

    def drawImage(self, bandpass, image=None, integrator='trapezoidal', **kwargs):
        """Base implementation for drawing an image of a ChromaticObject.

        Some subclasses may choose to override this for specific efficiency gains.  For instance,
        most GalSim use cases will probably finish with a convolution, in which case
        ChromaticConvolution.drawImage() will be used.

        The task of drawImage() in a chromatic context is to integrate a chromatic surface
        brightness profile multiplied by the throughput of `bandpass`, over the wavelength interval
        indicated by `bandpass`.

        Several integrators are available in galsim.integ to do this integration when using the
        first method (non-interpolated integration).  By default,
        `galsim.integ.SampleIntegrator(rule=np.trapz)` will be used if either
        `bandpass.wave_list` or `self.wave_list` have len() > 0.  If lengths of both are zero, which
        may happen if both the bandpass throughput and the SED associated with `self` are analytic
        python functions, for example, then `galsim.integ.ContinuousIntegrator(rule=np.trapz)`
        will be used instead.  This latter case by default will evaluate the integrand at 250
        equally-spaced wavelengths between `bandpass.blue_limit` and `bandpass.red_limit`.

        By default, the above two integrators will use the trapezoidal rule for integration.  The
        midpoint rule for integration can be specified instead by passing an integrator that has
        been initialized with the `rule=galsim.integ.midpt` argument.  When creating a
        ContinuousIntegrator, the number of samples `N` is also an argument.  For example:

            >>> integrator = galsim.ContinuousIntegrator(rule=galsim.integ.midpt, N=100)
            >>> image = chromatic_obj.drawImage(bandpass, integrator=integrator)

        Finally, this method uses a cache to avoid recomputing the integral over the product of
        the bandpass and object SED when possible (i.e., for separable profiles).  Because the
        cache size is finite, users may find that it is more efficient when drawing many images
        to group images using the same SEDs and bandpasses together in order to hit the cache more
        often.  The default cache size is 10, but may be resized using the
        `ChromaticObject.resize_multiplier_cache()` method.

        @param bandpass         A Bandpass object representing the filter against which to
                                integrate.
        @param image            Optionally, the Image to draw onto.  (See GSObject.drawImage()
                                for details.)  [default: None]
        @param integrator       When doing the exact evaluation of the profile, this argument should
                                be one of the image integrators from galsim.integ, or a string
                                'trapezoidal' or 'midpoint', in which case the routine will use a
                                SampleIntegrator or ContinuousIntegrator depending on whether or not
                                the object has a `wave_list`.  [default: 'trapezoidal',
                                which will try to select an appropriate integrator using the
                                trapezoidal integration rule automatically.]
        @param **kwargs         For all other kwarg options, see GSObject.drawImage()

        @returns the drawn Image.
        """
        # When drawing, we must be an SED'd object.  So check that here.
        if self.SED is None:
            raise ValueError("Can only draw ChromaticObjects with SEDs.")

        # setup output image using fiducial profile
        wave0, prof0 = self._fiducial_profile(bandpass)
        image = prof0.drawImage(image=image, setup_only=True, **kwargs)
        _remove_setup_kwargs(kwargs)

        # determine combined self.wave_list and bandpass.wave_list
        wave_list, _, _ = galsim.utilities.combine_wave_list([self, bandpass])

        if self.separable:
            multiplier = ChromaticObject._multiplier_cache(self.SED, bandpass, tuple(wave_list))
            prof0 *= multiplier/self.SED(wave0)
            image = prof0.drawImage(image=image, **kwargs)
            return image

        # Decide on integrator.  If the user passed one of the integrators from galsim.integ, that's
        # fine.  Otherwise we decide based on the adopted integration rule and the presence/absence
        # of `wave_list`.
        if isinstance(integrator, str):
            if integrator == 'trapezoidal':
                rule = np.trapz
            elif integrator == 'midpoint':
                rule = galsim.integ.midpt
            else:
                raise TypeError("Unrecognized integration rule: %s"%integrator)
            if len(wave_list) > 0:
                integrator = galsim.integ.SampleIntegrator(rule)
            else:
                integrator = galsim.integ.ContinuousIntegrator(rule)
        if not isinstance(integrator, galsim.integ.SampleIntegrator) and \
                not isinstance(integrator, galsim.integ.ContinuousIntegrator):
            raise TypeError("Invalid type passed in for integrator!")

        # merge self.wave_list into bandpass.wave_list if using a sampling integrator
        if isinstance(integrator, galsim.integ.SampleIntegrator):
            bandpass = galsim.Bandpass(galsim.LookupTable(wave_list, bandpass(wave_list),
                                                          interpolant='linear'), 'nm')

        add_to_image = kwargs.pop('add_to_image', False)
        integral = integrator(self.evaluateAtWavelength, bandpass, image, kwargs)

        # For performance profiling, store the number of evaluations used for the last integration
        # performed.  Note that this might not be very useful for ChromaticSum instances, which are
        # drawn one profile at a time, and hence _last_n_eval will only represent the final
        # component drawn.
        self._last_n_eval = integrator.last_n_eval

        # Apply integral to the initial image appropriately.
        # Note: Don't do image = integral and return that for add_to_image==False.
        #       Remember that python doesn't actually do assignments, so this won't update the
        #       original image if the user provided one.  The following procedure does work.
        if not add_to_image:
            image.setZero()
        image += integral
        return image

    def evaluateAtWavelength(self, wave):
        """Evaluate this chromatic object at a particular wavelength.

        @param wave     Wavelength in nanometers.

        @returns the monochromatic object at the given wavelength.
        """
        if self.__class__ != ChromaticObject:
            raise NotImplementedError(
                    "Subclasses of ChromaticObject must override evaluateAtWavelength()")
        return self.obj.evaluateAtWavelength(wave)

    def __mul__(self, flux_ratio):
        """Scale the flux of the object by the given flux ratio, which may be an SED, a float, or
        a univariate callable function (of wavelength) that returns a float.

        The normalization of ChromaticObjects is tracked through either the .SED attribute or the
        ._norm attribute, depending on whether the ChromaticObject units are
        photons/nm/cm^2/s/arcsec^2 or 1/arcsec^2, respectively.

        If flux_ratio is an SED, then self.SED must be None (essentially because you can't multiply
        two SEDs together and dimensionally get an SED as a result).  The returned object will have
        its SED attribute set (and ._norm=None).

        If flux_ratio is a float or univariate callable function, then which attribute used to track
        the normalization of the output (i.e., which of .SED or ._norm is not None) will be the same
        as in self.

        @param flux_ratio   The factor by which to scale the normalization of the object.
                            `flux_ratio` may be a float, univariate callable function, in which case
                            the argument should be wavelength in nanometers and return value the
                            flux ratio for that wavelength, or an SED.

        @returns a new object with scaled flux.
        """
        return self.withScaledFlux(flux_ratio)

    def withScaledFlux(self, flux_ratio):
        """Multiply the flux of the object by `flux_ratio`

        @param flux_ratio   The factor by which to scale the normalization of the object.
                            `flux_ratio` may be a float, univariate callable function, in which case
                            the argument should be wavelength in nanometers and return value the
                            flux ratio for that wavelength, or an SED.

        @returns a new object with scaled flux.
        """
        return galsim.Transform(self, flux_ratio=flux_ratio)

    def centroid(self, bandpass):
        """ Determine the centroid of the wavelength-integrated surface brightness profile.

        @param bandpass  The bandpass through which the observation is made.

        @returns the centroid of the integrated surface brightness profile, as a PositionD.
        """
        # if either the Bandpass or self maintain a wave_list, evaluate integrand only at
        # those wavelengths.
        if len(bandpass.wave_list) > 0 or len(self.wave_list) > 0:
            w, _, _ = galsim.utilities.combine_wave_list([self, bandpass])
            objs = [self.evaluateAtWavelength(y) for y in w]
            fluxes = [o.getFlux() for o in objs]
            centroids = [o.centroid() for o in objs]
            xcentroids = np.array([c.x for c in centroids])
            ycentroids = np.array([c.y for c in centroids])
            bp = bandpass(w)
            flux = np.trapz(bp * fluxes, w)
            xcentroid = np.trapz(bp * fluxes * xcentroids, w) / flux
            ycentroid = np.trapz(bp * fluxes * ycentroids, w) / flux
            return galsim.PositionD(xcentroid, ycentroid)
        else:
            flux_integrand = lambda w: self.evaluateAtWavelength(w).getFlux() * bandpass(w)
            def xcentroid_integrand(w):
                mono = self.evaluateAtWavelength(w)
                return mono.centroid().x * mono.getFlux() * bandpass(w)
            def ycentroid_integrand(w):
                mono = self.evaluateAtWavelength(w)
                return mono.centroid().y * mono.getFlux() * bandpass(w)
            flux = galsim.integ.int1d(flux_integrand, bandpass.blue_limit, bandpass.red_limit)
            xcentroid = 1./flux * galsim.integ.int1d(xcentroid_integrand,
                                                     bandpass.blue_limit,
                                                     bandpass.red_limit)
            ycentroid = 1./flux * galsim.integ.int1d(ycentroid_integrand,
                                                     bandpass.blue_limit,
                                                     bandpass.red_limit)
            return galsim.PositionD(xcentroid, ycentroid)

    def calculateFlux(self, bandpass):
        if self.SED is None:
            raise ValueError("Cannot calculate flux of ChromaticObject with .SED = None.")
        return self.SED.calculateFlux(bandpass)

    # Add together `ChromaticObject`s and/or `GSObject`s
    def __add__(self, other):
        return galsim.ChromaticSum([self, other])

    # Subtract `ChromaticObject`s and/or `GSObject`s
    def __sub__(self, other):
        return galsim.ChromaticSum([self, (-1. * other)])

    # Make op* and op*= work to adjust the flux of the object
    def __rmul__(self, other):
        return self.__mul__(other)

    # Likewise for op/ and op/=
    def __div__(self, other):
        return self.__mul__(1./other)

    def __truediv__(self, other):
        return self.__div__(other)

    # Following functions work to apply affine transformations to a ChromaticObject.
    #
    def expand(self, scale):
        """Expand the linear size of the profile by the given (possibly wavelength-dependent)
        scale factor `scale`, while preserving surface brightness.

        This doesn't correspond to either of the normal operations one would typically want to
        do to a galaxy.  The functions dilate() and magnify() are the more typical usage.  But this
        function is conceptually simple.  It rescales the linear dimension of the profile, while
        preserving surface brightness.  As a result, the flux will necessarily change as well.

        See dilate() for a version that applies a linear scale factor while preserving flux.

        See magnify() for a version that applies a scale factor to the area while preserving surface
        brightness.

        @param scale    The factor by which to scale the linear dimension of the object.  In
                        addition, `scale` may be a callable function, in which case the argument
                        should be wavelength in nanometers and the return value the scale for that
                        wavelength.

        @returns the expanded object
        """
        if hasattr(scale, '__call__'):
            def buildScaleJac(w):
                s = scale(w)
                return np.diag([s,s])
            jac = buildScaleJac
        else:
            jac = np.diag([scale, scale])
        return galsim.Transform(self, jac=jac)

    def dilate(self, scale):
        """Dilate the linear size of the profile by the given (possibly wavelength-dependent)
        `scale`, while preserving flux.

        e.g. `half_light_radius` <-- `half_light_radius * scale`

        See expand() and magnify() for versions that preserve surface brightness, and thus
        change the flux.

        @param scale    The linear rescaling factor to apply.  In addition, `scale` may be a
                        callable function, in which case the argument should be wavelength in
                        nanometers and the return value the scale for that wavelength.

        @returns the dilated object.
        """
        if hasattr(scale, '__call__'):
            return self.expand(scale).withScaledFlux(lambda w: 1./scale(w)**2)
        else:
            return self.expand(scale).withScaledFlux(1./scale**2)

    def magnify(self, mu):
        """Apply a lensing magnification, scaling the area and flux by `mu` at fixed surface
        brightness.

        This process applies a lensing magnification `mu`, which scales the linear dimensions of the
        image by the factor sqrt(mu), i.e., `half_light_radius` <-- `half_light_radius * sqrt(mu)`
        while increasing the flux by a factor of `mu`.  Thus, magnify() preserves surface
        brightness.

        See dilate() for a version that applies a linear scale factor while preserving flux.

        @param mu       The lensing magnification to apply.  In addition, `mu` may be a callable
                        function, in which case the argument should be wavelength in nanometers
                        and the return value the magnification for that wavelength.

        @returns the magnified object.
        """
        import math
        if hasattr(mu, '__call__'):
            return self.expand(lambda w: math.sqrt(mu(w)))
        else:
            return self.expand(math.sqrt(mu))

    def shear(self, *args, **kwargs):
        """Apply an area-preserving shear to this object, where arguments are either a Shear,
        or arguments that will be used to initialize one.

        For more details about the allowed keyword arguments, see the documentation for Shear
        (for doxygen documentation, see galsim.shear.Shear).

        The shear() method precisely preserves the area.  To include a lensing distortion with
        the appropriate change in area, either use shear() with magnify(), or use lens(), which
        combines both operations.

        Note that, while gravitational shear is monochromatic, the shear method may be used for
        many other use cases including some which may be wavelength-dependent, such as
        intrinsic galaxy shape, telescope dilation, atmospheric PSF shape, etc.  Thus, the
        shear argument is allowed to be a function of wavelength like other transformations.

        @param shear    The shear to be applied. Or, as described above, you may instead supply
                        parameters to construct a Shear directly.  eg. `obj.shear(g1=g1,g2=g2)`.
                        In addition, the `shear` parameter may be a callable function, in which
                        case the argument should be wavelength in nanometers and the return value
                        the shear for that wavelength, returned as a galsim.Shear instance.

        @returns the sheared object.
        """
        if len(args) == 1:
            if kwargs:
                raise TypeError("Gave both unnamed and named arguments!")
            if not hasattr(args[0], '__call__') and not isinstance(args[0], galsim.Shear):
                raise TypeError("Unnamed argument is not a Shear or function returning Shear!")
            shear = args[0]
        elif len(args) > 1:
            raise TypeError("Too many unnamed arguments!")
        elif 'shear' in kwargs:
            # Need to break this out specially in case it is a function of wavelength
            shear = kwargs.pop('shear')
            if kwargs:
                raise TypeError("Too many kwargs provided!")
        else:
            shear = galsim.Shear(**kwargs)
        if hasattr(shear, '__call__'):
            jac = lambda w: shear(w).getMatrix()
        else:
            jac = shear.getMatrix()
        return galsim.Transform(self, jac=jac)

    def lens(self, g1, g2, mu):
        """Apply a lensing shear and magnification to this object.

        This ChromaticObject method applies a lensing (reduced) shear and magnification.  The shear
        must be specified using the g1, g2 definition of shear (see Shear documentation for more
        details).  This is the same definition as the outputs of the PowerSpectrum and NFWHalo
        classes, which compute shears according to some lensing power spectrum or lensing by an NFW
        dark matter halo.  The magnification determines the rescaling factor for the object area and
        flux, preserving surface brightness.

        While gravitational lensing is achromatic, we do allow the parameters `g1`, `g2`, and `mu`
        to be callable functions to be parallel to all the other transformations of chromatic
        objects.  In this case, the functions should take the wavelength in nanometers as the
        argument, and the return values are the corresponding value at that wavelength.

        @param g1       First component of lensing (reduced) shear to apply to the object.
        @param g2       Second component of lensing (reduced) shear to apply to the object.
        @param mu       Lensing magnification to apply to the object.  This is the factor by which
                        the solid angle subtended by the object is magnified, preserving surface
                        brightness.

        @returns the lensed object.
        """
        if any(hasattr(g, '__call__') for g in [g1,g2]):
            _g1 = g1
            _g2 = g2
            if not hasattr(g1, '__call__'): _g1 = lambda w: g1
            if not hasattr(g2, '__call__'): _g2 = lambda w: g2
            S = lambda w: galsim.Shear(g1=_g1(w), g2=_g2(w))
            sheared = self.shear(S)
        else:
            sheared = self.shear(g1=g1,g2=g2)
        return sheared.magnify(mu)

    def rotate(self, theta):
        """Rotate this object by an Angle `theta`.

        @param theta    Rotation angle (Angle object, +ve anticlockwise). In addition, `theta` may
                        be a callable function, in which case the argument should be wavelength in
                        nanometers and the return value the rotation angle for that wavelength,
                        returned as a galsim.Angle instance.

        @returns the rotated object.
        """
        if hasattr(theta, '__call__'):
            def buildRMatrix(w):
                sth, cth = theta(w).sincos()
                R = np.array([[cth, -sth],
                              [sth,  cth]], dtype=float)
                return R
            jac = buildRMatrix
        else:
            sth, cth = theta.sincos()
            jac = np.array([[cth, -sth],
                            [sth,  cth]], dtype=float)
        return galsim.Transform(self, jac=jac)

    def transform(self, dudx, dudy, dvdx, dvdy):
        """Apply a transformation to this object defined by an arbitrary Jacobian matrix.

        This works the same as GSObject.transform(), so see that method's docstring for more
        details.

        As with the other more specific chromatic trasnformations, dudx, dudy, dvdx, and dvdy
        may be callable functions, in which case the argument should be wavelength in nanometers
        and the return value the appropriate value for that wavelength.

        @param dudx     du/dx, where (x,y) are the current coords, and (u,v) are the new coords.
        @param dudy     du/dy, where (x,y) are the current coords, and (u,v) are the new coords.
        @param dvdx     dv/dx, where (x,y) are the current coords, and (u,v) are the new coords.
        @param dvdy     dv/dy, where (x,y) are the current coords, and (u,v) are the new coords.

        @returns the transformed object.
        """
        if any(hasattr(dd, '__call__') for dd in [dudx, dudy, dvdx, dvdy]):
            _dudx = dudx
            _dudy = dudy
            _dvdx = dvdx
            _dvdy = dvdy
            if not hasattr(dudx, '__call__'): _dudx = lambda w: dudx
            if not hasattr(dudy, '__call__'): _dudy = lambda w: dudy
            if not hasattr(dvdx, '__call__'): _dvdx = lambda w: dvdx
            if not hasattr(dvdy, '__call__'): _dvdy = lambda w: dvdy
            jac = lambda w: np.array([[_dudx(w), _dudy(w)],
                                      [_dvdx(w), _dvdy(w)]], dtype=float)
        else:
            jac = np.array([[dudx, dudy],
                            [dvdx, dvdy]], dtype=float)
        return galsim.Transform(self, jac=jac)

    def shift(self, *args, **kwargs):
        """Apply a (possibly wavelength-dependent) (dx, dy) shift to this chromatic object.

        For a wavelength-independent shift, you may supply `dx,dy` as either two arguments, as a
        tuple, or as a PositionD or PositionI object.

        For a wavelength-dependent shift, you may supply two functions of wavelength in nanometers
        which will be interpreted as `dx(wave)` and `dy(wave)`, or a single function of wavelength
        in nanometers that returns either a 2-tuple, PositionD, or PositionI.

        @param dx   Horizontal shift to apply (float or function).
        @param dy   Vertical shift to apply (float or function).

        @returns the shifted object.

        """
        # This follows along the galsim.utilities.pos_args function, but has some
        # extra bits to account for the possibility of dx,dy being functions.
        # First unpack args/kwargs
        if len(args) == 0:
            # Then dx,dy need to be kwargs
            # If not, then python will raise an appropriate error.
            dx = kwargs.pop('dx')
            dy = kwargs.pop('dy')
            offset = None
        elif len(args) == 1:
            if hasattr(args[0], '__call__'):
                try:
                    args[0](700.).x
                    # If the function returns a Position, recast it as a function returning
                    # a numpy array.
                    def offset_func(w):
                        d = args[0](w)
                        return np.asarray( (d.x, d.y) )
                    offset = offset_func
                except:
                    # Then it's a function returning a tuple or list or array.
                    # Just make sure it is actually an array to make our life easier later.
                    offset = lambda w: np.asarray(args[0](w))
            elif isinstance(args[0], galsim.PositionD) or isinstance(args[0], galsim.PositionI):
                offset = np.asarray( (args[0].x, args[0].y) )
            else:
                # Let python raise the appropriate exception if this isn't valid.
                offset = np.asarray(args[0])
        elif len(args) == 2:
            dx = args[0]
            dy = args[1]
            offset = None
        else:
            raise TypeError("Too many arguments supplied!")
        if kwargs:
            raise TypeError("Got unexpected keyword arguments: %s",kwargs.keys())

        if offset is None:
            offset = galsim.utilities.functionize(lambda x,y:(x,y))(dx, dy)

        return galsim.Transform(self, offset=offset)

ChromaticObject._multiplier_cache = galsim.utilities.LRU_Cache(
    ChromaticObject._get_multiplier, maxsize=10)


class InterpolatedChromaticObject(ChromaticObject):
    """A ChromaticObject that uses interpolation of predrawn images to speed up subsequent
    rendering.

    This class wraps another ChromaticObject, which is stored in the attribute `original`.
    Any ChromaticObject can be used, although the interpolation procedure is most effective
    for non-separable objects, which can sometimes be very slow to render.

    Normally, you would not create an InterpolatedChromaticObject directly.  It is the
    return type from `chrom_obj.interpolate()`.  See the description of that function
    for more details.

    @param original         The ChromaticObject to be interpolated.
    @param waves            The list, tuple, or NumPy array of wavelengths to be used when
                            building up the grid of images for interpolation.  The wavelengths
                            should be given in nanometers, and they should span the full range
                            of wavelengths covered by any bandpass to be used for drawing Images
                            (i.e., this class will not extrapolate beyond the given range of
                            wavelengths).  They can be spaced any way the user likes, not
                            necessarily linearly, though interpolation will be linear in
                            wavelength between the specified wavelengths.
    @param oversample_fac   Factor by which to oversample the stored profiles compared to the
                            default, which is to sample them at the Nyquist frequency for
                            whichever wavelength has the highest Nyquist frequency.
                            `oversample_fac`>1 results in higher accuracy but costlier
                            pre-computations (more memory and time). [default: 1]
    """
    def __init__(self, original, waves, oversample_fac=1.0):

        self.separable = original.separable
        self.interpolated = True
        self.SED = original.SED
        self._norm = original._norm
        self.wave_list = original.wave_list

        # Don't interpolate an interpolation.  Go back to the original.
        self.original = original.deinterpolated
        self.waves = np.sort(np.array(waves))
        self.oversample = oversample_fac

        # Make the objects between which we are going to interpolate.  Note that these do not have
        # to be saved for later, unlike the images.
        objs = [ original.evaluateAtWavelength(wave) for wave in self.waves ]

        # Find the Nyquist scale for each, and to be safe, choose the minimum value to use for the
        # array of images that is being stored.
        nyquist_scale_vals = [ obj.nyquistScale() for obj in objs ]
        scale = np.min(nyquist_scale_vals) / oversample_fac

        # Find the suggested image size for each object given the choice of scale, and use the
        # maximum just to be safe.
        possible_im_sizes = [ obj.SBProfile.getGoodImageSize(scale, 1.0) for obj in objs ]
        im_size = np.max(possible_im_sizes)

        # Find the stepK and maxK values for each object.  These will be used later on, so that we
        # can force these values when instantiating InterpolatedImages before drawing.
        self.stepK_vals = [ obj.stepK() for obj in objs ]
        self.maxK_vals = [ obj.maxK() for obj in objs ]

        # Finally, now that we have an image scale and size, draw all the images.  Note that
        # `no_pixel` is used (we want the object on its own, without a pixel response).
        self.ims = [ obj.drawImage(scale=scale, nx=im_size, ny=im_size, method='no_pixel')
                     for obj in objs ]

    def _deinterpolate(self):
        return self.original

    def __eq__(self, other):
        return (isinstance(other, galsim.InterpolatedChromaticObject) and
                self.original == other.original and
                np.array_equal(self.waves, other.waves) and
                self.oversample == other.oversample)

    def __hash__(self):
        return hash(("galsim.InterpolatedChromaticObject", self.original, tuple(self.waves),
                     self.oversample))

    def __repr__(self):
        s = 'galsim.InterpolatedChromaticObject(%r,%r'%(self.original, self.waves)
        if self.oversample != 1.0:
            s += ', oversample_fac=%r'%self.oversample
        s += ')'
        return s

    def __str__(self):
        return 'galsim.InterpolatedChromaticObject(%s,%s)'%(self.original, self.waves)

    def _imageAtWavelength(self, wave):
        """
        Get an image of the object at a particular wavelength, using linear interpolation between
        the originally-stored images.  Also returns values for step_k and max_k, to be used to
        expedite the instantation of InterpolatedImages.

        @param wave     Wavelength in nanometers.

        @returns an Image of the object at the given wavelength.
        """
        # First, some wavelength-related sanity checks.
        if wave < np.min(self.waves) or wave > np.max(self.waves):
            raise RuntimeError("Requested wavelength %.1f is outside the allowed range:"
                               " %.1f to %.1f nm"%(wave, np.min(self.waves), np.max(self.waves)))

        # Figure out where the supplied wavelength is compared to the list of wavelengths on which
        # images were originally tabulated.
        lower_idx, frac = _findWave(self.waves, wave)

        # Actually do the interpolation for the image, stepK, and maxK.
        im = _linearInterp(self.ims, frac, lower_idx)
        stepk = _linearInterp(self.stepK_vals, frac, lower_idx)
        maxk = _linearInterp(self.maxK_vals, frac, lower_idx)

        return im, stepk, maxk

    def evaluateAtWavelength(self, wave):
        """
        Evaluate this ChromaticObject at a particular wavelength using interpolation.

        @param wave     Wavelength in nanometers.

        @returns the monochromatic object at the given wavelength, as a GSObject.
        """
        im, stepk, maxk = self._imageAtWavelength(wave)
        return galsim.InterpolatedImage(im, _force_stepk=stepk, _force_maxk=maxk)

    def _get_interp_image(self, bandpass, image=None, integrator='trapezoidal', **kwargs):
        """Draw method adapted to work for ChromaticImage instances for which interpolation between
        stored images is being used.  Users should not call this routine directly, and should
        instead interact with the `drawImage` method.
        """
        if integrator not in ['trapezoidal', 'midpoint']:
            if not isinstance(integrator, str):
                raise TypeError("Integrator should be a string indicating trapezoidal"
                                " or midpoint rule for integration")
            raise TypeError("Unknown integrator: %s"%integrator)

        # setup output image (semi-arbitrarily using the bandpass effective wavelength).
        # Note: we cannot just use self._imageAtWavelength, because that routine returns an image
        # with whatever pixel scale was required to sample all the images properly.  We want to set
        # up an output image that has the requested pixel scale, which might change the image size
        # and so on.
        _, prof0 = self._fiducial_profile(bandpass)
        image = prof0.drawImage(image=image, setup_only=True, **kwargs)
        _remove_setup_kwargs(kwargs)

        # determine combination of self.wave_list and bandpass.wave_list
        wave_list, _, _ = galsim.utilities.combine_wave_list([self, bandpass])

        if np.min(wave_list) < np.min(self.waves):
            raise RuntimeError("Requested wavelength %.1f is outside the allowed range:"
                               " %.1f to %.1f nm"%(np.min(wave_list), np.min(self.waves),
                                                   np.max(self.waves)))
        if np.max(wave_list) > np.max(self.waves):
            raise RuntimeError("Requested wavelength %.1f is outside the allowed range:"
                               " %.1f to %.1f nm"%(np.max(wave_list), np.min(self.waves),
                                                   np.max(self.waves)))

        # The integration is carried out using the following two basic principles:
        # (1) We use linear interpolation between the stored images to get an image at a given
        #     wavelength.
        # (2) We use the trapezoidal or midpoint rule for integration, depending on what the user
        #     has selected.

        # For the midpoint rule, we take the list of wavelengths in wave_list, and treat each of
        # those as the midpoint of a narrow wavelength range with width given by `dw` (to be
        # calculated below).  Then, we can take the summation over indices i:
        #   integral ~ sum_i dw[i] * img[i].
        # where the indices i run over the wavelengths in wave_list from i=0...N-1.
        #
        # For the trapezoidal rule, we treat the list of wavelengths in wave_list as the *edges* of
        # the regions, and sum over the areas of the trapezoids, giving
        #   integral ~ sum_j dw[j] * img[j] + sum_k dw[k] *img[k]/2.
        # where indices j go from j=1...N-2 and k is (0, N-1).

        # Figure out the dwave for each of the wavelengths in the combined wave_list.
        dw = [wave_list[1]-wave_list[0]]
        dw.extend(0.5*(wave_list[2:]-wave_list[0:-2]))
        dw.append(wave_list[-1]-wave_list[-2])
        # Set up arrays to accumulate the weights for each of the stored images.
        weight_fac = np.zeros(len(self.waves))
        for idx, w in enumerate(wave_list):
            # Find where this is with respect to the wavelengths on which images are stored.
            lower_idx, frac = _findWave(self.waves, w)
            # Store the weight factors for the two stored images that can contribute at this
            # wavelength.  Must include the dwave that is part of doing the integral.
            b = bandpass(w) * dw[idx]

            if (idx > 0 and idx < len(wave_list)-1) or integrator == 'midpoint':
                weight_fac[lower_idx] += (1.0-frac)*b
                weight_fac[lower_idx+1] += frac*b
            else:
                # We're doing the trapezoidal rule, and we're at the endpoints.
                weight_fac[lower_idx] += (1.0-frac)*b/2.
                weight_fac[lower_idx+1] += frac*b/2.

        # Do the integral as a weighted sum.
        integral = sum([w*im for w,im in zip(weight_fac, self.ims)])

        # Figure out stepK and maxK using the minimum and maximum (respectively) that have nonzero
        # weight.  This is the most conservative possible choice, since it's possible that some of
        # the images that have non-zero weights might have such tiny weights that they don't change
        # the effective stepk and maxk we should use.
        stepk = np.min(np.array(self.stepK_vals)[weight_fac>0])
        maxk = np.max(np.array(self.maxK_vals)[weight_fac>0])

        # Instantiate the InterpolatedImage, using these conservative stepK and maxK choices.
        return galsim.InterpolatedImage(integral, _force_stepk=stepk, _force_maxk=maxk)

    def drawImage(self, bandpass, image=None, integrator='trapezoidal', **kwargs):
        """Draw an image as seen through a particular bandpass using the stored interpolated
        images at the specified wavelengths.

        This integration will take place using interpolation between stored images that were
        setup when the object was constructed.  (See interpolate() for more details.)

        @param bandpass         A Bandpass object representing the filter against which to
                                integrate.
        @param image            Optionally, the Image to draw onto.  (See GSObject.drawImage()
                                for details.)  [default: None]
        @param integrator       The integration algorithm to use, given as a string.  Either
                                'midpoint' or 'trapezoidal' is allowed. [default: 'trapezoidal']
        @param **kwargs         For all other kwarg options, see GSObject.drawImage()

        @returns the drawn Image.
        """
        # When drawing, we must be an SED'd object.  So check that here.
        if self.SED is None:
            raise ValueError("Can only draw ChromaticObjects with SEDs.")

        int_im = self._get_interp_image(bandpass, image=image, integrator=integrator, **kwargs)
        image = int_im.drawImage(image=image, **kwargs)
        return image


class ChromaticAtmosphere(ChromaticObject):
    """A ChromaticObject implementing two atmospheric chromatic effects: differential
    chromatic refraction (DCR) and wavelength-dependent seeing.

    Due to DCR, blue photons land closer to the zenith than red photons.  Kolmogorov turbulence
    also predicts that blue photons get spread out more by the atmosphere than red photons,
    specifically FWHM is proportional to wavelength^(-0.2).  Both of these effects can be
    implemented by wavelength-dependent shifts and dilations.

    Since DCR depends on the zenith angle and the parallactic angle (which is the position angle of
    the zenith measured from North through East) of the object being drawn, these must be specified
    via keywords.  There are four ways to specify these values:
      1) explicitly provide `zenith_angle = ...` as a keyword of type Angle, and
         `parallactic_angle` will be assumed to be 0 by default.
      2) explicitly provide both `zenith_angle = ...` and `parallactic_angle = ...` as
         keywords of type Angle.
      3) provide the coordinates of the object `obj_coord = ...` and the coordinates of the zenith
         `zenith_coord = ...` as keywords of type CelestialCoord.
      4) provide the coordinates of the object `obj_coord = ...` as a CelestialCoord, the
         hour angle of the object `HA = ...` as an Angle, and the latitude of the observer
         `latitude = ...` as an Angle.

    DCR also depends on temperature, pressure and water vapor pressure of the atmosphere.  The
    default values for these are expected to be appropriate for LSST at Cerro Pachon, Chile, but
    they are broadly reasonable for most observatories.

    Note that a ChromaticAtmosphere by itself is NOT the correct thing to use to draw an image of a
    star. Stars (and galaxies too, of course) have an SED that is not flat. To draw a real star, you
    should either multiply the ChromaticAtmosphere object by an SED, or convolve it with a point
    source (typically approximated by a very tiny Gaussian) multiplied by an SED:

        >>> psf = galsim.ChromaticAtmosphere(...)
        >>> star = galsim.Gaussian(sigma = 1.e-6) * psf_sed
        >>> final_star = galsim.Convolve( [psf, star] )
        >>> final_star.drawImage(bandpass = bp, ...)

    @param base_obj             Fiducial PSF, equal to the monochromatic PSF at `base_wavelength`
    @param base_wavelength      Wavelength represented by the fiducial PSF, in nanometers.
    @param scale_unit           Units used by base_obj for its linear dimensions.
                                [default: galsim.arcsec]
    @param alpha                Power law index for wavelength-dependent seeing.  [default: -0.2,
                                the prediction for Kolmogorov turbulence]
    @param zenith_angle         Angle from object to zenith, expressed as an Angle
                                [default: 0]
    @param parallactic_angle    Parallactic angle, i.e. the position angle of the zenith, measured
                                from North through East.  [default: 0]
    @param obj_coord            Celestial coordinates of the object being drawn as a
                                CelestialCoord. [default: None]
    @param zenith_coord         Celestial coordinates of the zenith as a CelestialCoord.
                                [default: None]
    @param HA                   Hour angle of the object as an Angle. [default: None]
    @param latitude             Latitude of the observer as an Angle. [default: None]
    @param pressure             Air pressure in kiloPascals.  [default: 69.328 kPa]
    @param temperature          Temperature in Kelvins.  [default: 293.15 K]
    @param H2O_pressure         Water vapor pressure in kiloPascals.  [default: 1.067 kPa]
    """
    # Note that this class *always* has self.SED = None, and is therefore not drawable.
    def __init__(self, base_obj, base_wavelength, scale_unit=galsim.arcsec, **kwargs):

        self.separable = False
        self.interpolated = False
        self.SED = None
        self._norm = 1.0
        self.wave_list = np.array([], dtype=float)

        self.base_obj = base_obj
        self.base_wavelength = base_wavelength

        if isinstance(scale_unit, str):
            scale_unit = galsim.angle.get_angle_unit(scale_unit)
        self.scale_unit = scale_unit

        self.alpha = kwargs.pop('alpha', -0.2)
        # Determine zenith_angle and parallactic_angle from kwargs
        if 'zenith_angle' in kwargs:
            self.zenith_angle = kwargs.pop('zenith_angle')
            self.parallactic_angle = kwargs.pop('parallactic_angle', 0.0*galsim.degrees)
            if not isinstance(self.zenith_angle, galsim.Angle) or \
                    not isinstance(self.parallactic_angle, galsim.Angle):
                raise TypeError("zenith_angle and parallactic_angle must be galsim.Angles!")
        elif 'obj_coord' in kwargs:
            obj_coord = kwargs.pop('obj_coord')
            if 'zenith_coord' in kwargs:
                zenith_coord = kwargs.pop('zenith_coord')
                self.zenith_angle, self.parallactic_angle = galsim.dcr.zenith_parallactic_angles(
                    obj_coord=obj_coord, zenith_coord=zenith_coord)
            else:
                if 'HA' not in kwargs or 'latitude' not in kwargs:
                    raise TypeError("ChromaticAtmosphere requires either zenith_coord or (HA, "
                                    +"latitude) when obj_coord is specified!")
                HA = kwargs.pop('HA')
                latitude = kwargs.pop('latitude')
                self.zenith_angle, self.parallactic_angle = galsim.dcr.zenith_parallactic_angles(
                    obj_coord=obj_coord, HA=HA, latitude=latitude)
        else:
            raise TypeError("Need to specify zenith_angle and parallactic_angle!")

        # Any remaining kwargs will get forwarded to galsim.dcr.get_refraction
        # Check that they're valid
        for kw in kwargs:
            if kw not in ['temperature', 'pressure', 'H2O_pressure']:
                raise TypeError("Got unexpected keyword: {0}".format(kw))
        self.kw = kwargs

        self.base_refraction = galsim.dcr.get_refraction(self.base_wavelength, self.zenith_angle,
                                                         **kwargs)

    def _deinterpolate(self):
        return self

    def __eq__(self, other):
        return (isinstance(other, galsim.ChromaticAtmosphere) and
                self.base_obj == other.base_obj and
                self.base_wavelength == other.base_wavelength and
                self.alpha == other.alpha and
                self.zenith_angle == other.zenith_angle and
                self.parallactic_angle == other.parallactic_angle and
                self.kw == other.kw)

    def __hash__(self):
        return hash(("galsim.ChromaticAtmosphere", self.base_obj, self.base_wavelength,
                     self.alpha, self.zenith_angle, self.parallactic_angle,
                     frozenset(self.kw.items())))

    def __repr__(self):
        s = 'galsim.ChromaticAtmosphere(%r, base_wavelength=%r, alpha=%r'%(
                self.base_obj, self.base_wavelength, self.alpha)
        s += ', zenith_angle=%r, parallactic_angle=%r'%(self.zenith_angle, self.parallactic_angle)
        for k,v in self.kw.items():
            s += ', %s=%r'%(k,v)
        s += ')'
        return s

    def __str__(self):
        return 'galsim.ChromaticAtmosphere(%s, base_wavelength=%s, alpha=%s)'%(
                self.base_obj, self.base_wavelength, self.alpha)

    def build_obj(self):
        """Build a ChromaticTransformation object for this ChromaticAtmosphere.

        We don't do this right away to help make ChromaticAtmosphere objects be picklable.
        Building this is quite fast, so we do it on the fly in evaluateAtWavelength and
        drawImage.
        """
        def shift_fn(w):
            shift_magnitude = galsim.dcr.get_refraction(w, self.zenith_angle, **self.kw)
            shift_magnitude -= self.base_refraction
            shift_magnitude = shift_magnitude * galsim.radians / self.scale_unit
            sinp, cosp = self.parallactic_angle.sincos()
            shift = (-shift_magnitude * sinp, shift_magnitude * cosp)
            return shift

        def jac_fn(w):
            scale = (w/self.base_wavelength)**self.alpha
            return np.diag([scale, scale])

        flux_ratio = lambda w: (w/self.base_wavelength)**(-2.*self.alpha)

        return ChromaticTransformation(self.base_obj, jac=jac_fn, offset=shift_fn,
                                       flux_ratio=flux_ratio)

    def evaluateAtWavelength(self, wave):
        """Evaluate this chromatic object at a particular wavelength.

        @param wave     Wavelength in nanometers.

        @returns the monochromatic object at the given wavelength.
        """
        return self.build_obj().evaluateAtWavelength(wave)


class Chromatic(ChromaticObject):
    """Construct chromatic versions of galsim GSObjects.

    This class attaches an SED to a galsim GSObject.  This is useful to consistently generate
    the same galaxy observed through different filters, or, with the ChromaticSum class, to
    construct multi-component galaxies, each with a different SED. For example, a bulge+disk galaxy
    could be constructed:

        >>> bulge_SED = user_function_to_get_bulge_spectrum()
        >>> disk_SED = user_function_to_get_disk_spectrum()
        >>> bulge_mono = galsim.DeVaucouleurs(half_light_radius=1.0)
        >>> disk_mono = galsim.Exponential(half_light_radius=2.0)
        >>> bulge = galsim.Chromatic(bulge_mono, bulge_SED)
        >>> disk = galsim.Chromatic(disk_mono, disk_SED)
        >>> gal = bulge + disk

    Some syntactic sugar available for creating Chromatic instances is simply to multiply
    a GSObject instance by an SED instance.  Thus the last three lines above are equivalent to:

        >>> gal = bulge_mono * bulge_SED + disk_mono * disk_SED

    The SED is usually specified as a galsim.SED object, though any callable that returns
    spectral density in photons/nanometer as a function of wavelength in nanometers should work.

    Typically, the SED describes the flux in photons per nanometer of an object with a particular
    magnitude, possibly normalized with either the method sed.withFlux() or sed.withMagnitude()
    (see the docstrings in the SED class for details about these and other normalization options).
    Then the `flux` attribute of the GSObject should just be the _relative_ flux scaling of the
    current object compared to that normalization.  This implies (at least) two possible
    conventions.
    1. You can normalize the SED to have unit flux with `sed = sed.withFlux(1.0, bandpass)`. Then
    the `flux` of each GSObject would be the actual flux in photons when observed in the given
    bandpass.
    2. You can leave the object flux as 1 (the default for most types when you construct them) and
    set the flux in the SED with `sed = sed.withFlux(flux, bandpass)`.  Then if the object had
    `flux` attribute different from 1, it would just refer to the factor by which that particular
    object is brighter than the value given in the normalization command.

    Initialization
    --------------

    @param gsobj    A GSObject instance to be chromaticized.
    @param SED      Typically an SED object, though any callable that returns
                    spectral density in photons/nanometer as a function of wavelength
                    in nanometers should work.
    """
    def __init__(self, gsobj, SED):
        flux = gsobj.getFlux()
        self.SED = SED * flux
        self._norm = None
        self.obj = gsobj / flux
        self.wave_list = SED.wave_list
        # Chromaticized GSObjects are separable into spatial (x,y) and spectral (lambda) factors.
        self.separable = True
        self.interpolated = False

    def _deinterpolate(self):
        return self

    def __eq__(self, other):
        return (isinstance(other, galsim.Chromatic) and
                self.obj == other.obj and
                self.SED == other.SED)

    def __hash__(self):
        return hash(("galsim.Chromatic", self.obj, self.SED))

    def __repr__(self):
        return 'galsim.Chromatic(%r,%r)'%(self.obj, self.SED)

    def __str__(self):
        return 'galsim.Chromatic(%s,%s)'%(self.obj, self.SED)

    def evaluateAtWavelength(self, wave):
        """Evaluate this chromatic object at a particular wavelength.

        @param wave  Wavelength in nanometers.

        @returns the monochromatic object at the given wavelength.
        """
        return self.SED(wave) * self.obj


class ChromaticTransformation(ChromaticObject):
    """A class for modeling a wavelength-dependent affine transformation of a ChromaticObject
    instance.

    Initialization
    --------------

    Typically, you do not need to construct a ChromaticTransformation object explicitly.
    This is the type returned by the various transformation methods of ChromaticObject such as
    shear(), rotate(), shift(), transform(), etc.  All the various transformations can be described
    as a combination of transform() and shift(), which are described by (dudx,dudy,dvdx,dvdy) and
    (dx,dy) respectively.

    @param obj              The object to be transformed.
    @param jac              A list or tuple ( dudx, dudy, dvdx, dvdy ), or a numpy.array object
                            [[dudx, dudy], [dvdx, dvdy]] describing the Jacobian to apply.  May
                            also be a function of wavelength returning a numpy array.
                            [default: (1,0,0,1)]
    @param offset           A galsim.PositionD or list or tuple or numpy array giving the offset by
                            which to shift the profile.  May also be a function of wavelength
                            returning a numpy array.  [default: (0,0)]
    @param flux_ratio       A factor by which to multiply the flux of the object. [default: 1]
    @param gsparams         An optional GSParams argument.  See the docstring for GSParams for
                            details. [default: None]
    """
    def __init__(self, obj, jac=np.identity(2), offset=(0,0), flux_ratio=1., gsparams=None):
        if isinstance(offset, galsim.PositionD) or isinstance(offset, galsim.PositionI):
            offset = (offset.x, offset.y)
        if not hasattr(jac,'__call__'):
            jac = np.asarray(jac).reshape(2,2)
        if not hasattr(offset,'__call__'):
            offset = np.asarray(offset)

        self.chromatic = (hasattr(jac,'__call__') or hasattr(offset,'__call__') or
                          hasattr(flux_ratio,'__call__'))
        # Technically, if the only chromatic transformation is a flux_ratio, and the original object
        # is separable, then the transformation is still separable (for instance, galsim.Chromatic),
        # but we'll ignore that here.
        self.separable = obj.separable and not self.chromatic

        if isinstance(flux_ratio, galsim.SED):
            if obj.SED is not None:
                raise ValueError("Cannot attach more than one SED to ChromaticObject.")
            else:
                self.SED = obj._norm * flux_ratio
                self._norm = None
        else:  # either scalar or generic callable
            if obj.SED is not None:
                self.SED = obj.SED * flux_ratio
                self._norm = None
            else:  # non-SED times non-SED, so set ._norm
                self.SED = None
                @galsim.utilities.functionize
                def normprod(norm1, norm2):
                    return norm1 * norm2
                self._norm = normprod(obj._norm, flux_ratio)

        if obj.interpolated and self.chromatic:
            import warnings
            warnings.warn("Cannot render image with chromatic transformation applied to it "
                          "using interpolation between stored images.  Reverting to "
                          "non-interpolated version.")
            obj = obj.deinterpolated
        self.interpolated = obj.interpolated

        if isinstance(obj, ChromaticTransformation):
            self.original = obj.original

            @galsim.utilities.functionize
            def new_jac(jac1, jac2):
                return jac2.dot(jac1)

            @galsim.utilities.functionize
            def new_offset(jac2, off1, off2):
                return jac2.dot(off1) + off2

            @galsim.utilities.functionize
            def new_flux_ratio(flx1, flx2):
                return flx1 * flx2

            self._jac = new_jac(obj._jac, jac)
            self._offset = new_offset(jac, obj._offset, offset)
            self._flux_ratio = new_flux_ratio(obj._flux_ratio, flux_ratio)

        else:
            self.original = obj
            self._jac = jac
            self._offset = offset
            self._flux_ratio = flux_ratio

        if self.SED is not None:
            self.wave_list, _, _ = galsim.utilities.combine_wave_list(self.original, self.SED)
        else:
            self.wave_list = self.original.wave_list

        if gsparams is None:
            if hasattr(self.original, 'gsparams'):
                self.gsparams = self.original.gsparams
            else:
                self.gsparams = None
        else:
            self.gsparams = gsparams

    # There's really no good way to check that two callables are equal, except if they literally
    # point to the same object.  So we'll just check for that for _jac, _offset, and _flux_ratio.
    def __eq__(self, other):
        if not (isinstance(other, galsim.ChromaticTransformation) and
                self.original == other.original and
                self.gsparams == other.gsparams):
            return False
        for attr in ['_jac', '_offset', '_flux_ratio']:
            selfattr = getattr(self, attr)
            otherattr = getattr(other, attr)
            # For this attr, either both need to be chromatic or neither.
            if ((hasattr(selfattr, '__call__') and not hasattr(otherattr, '__call__')) or
                (hasattr(otherattr, '__call__') and not hasattr(selfattr, '__call__'))):
                return False
            # If chromatic, then check that attrs compare equal
            if hasattr(selfattr, '__call__'):
                if selfattr != otherattr:
                    return False
            else: # Otherwise, check that attr arrays (or _flux_ratio float) are equal.
                if not np.array_equal(selfattr, otherattr):
                    return False
        return True

    def __hash__(self):
        # This one's a bit complicated, so we'll go ahead and cache the hash.
        if not hasattr(self, '_hash'):
            self._hash = hash(("galsim.ChromaticTransformation", self.original, self._flux_ratio,
                               self.gsparams))
            # achromatic _jac and _offset are ndarrays, so need to be handled separately.
            for attr in ['_jac', '_offset']:
                selfattr = getattr(self, attr)
                if hasattr(selfattr, '__call__'):
                    self._hash ^= hash(selfattr)
                else:
                    self._hash ^= hash(tuple(selfattr.ravel().tolist()))
        return self._hash

    def __repr__(self):
        if hasattr(self._jac, '__call__'):
            jac = self._jac
        else:
            jac = self._jac.ravel().tolist()
        if hasattr(self._offset, '__call__'):
            offset = self._offset
        else:
            offset = galsim.PositionD(*(self._offset.tolist()))
        return 'galsim.ChromaticTransformation(%r, jac=%r, offset=%r, flux_ratio=%r, gsparams=%r)'%(
            self.original, jac, offset, self._flux_ratio, self.gsparams)

    def __str__(self):
        s = str(self.original)
        if hasattr(self._jac, '__call__'):
            s += '.transform(%s)'%self._jac
        else:
            dudx, dudy, dvdx, dvdy = self._jac.ravel()
            if dudx != 1 or dudy != 0 or dvdx != 0 or dvdy != 1:
                # Figure out the shear/rotate/dilate calls that are equivalent.
                jac = galsim.JacobianWCS(dudx,dudy,dvdx,dvdy)
                scale, shear, theta, flip = jac.getDecomposition()
                single = None
                if flip:
                    single = 0  # Special value indicating to just use transform.
                if abs(theta.rad()) > 1.e-12:
                    if single is None:
                        single = '.rotate(%s)'%theta
                    else:
                        single = 0
                if shear.getG() > 1.e-12:
                    if single is None:
                        single = '.shear(%s)'%shear
                    else:
                        single = 0
                if abs(scale-1.0) > 1.e-12:
                    if single is None:
                        single = '.expand(%s)'%scale
                    else:
                        single = 0
                if single == 0:
                    single = '.transform(%s,%s,%s,%s)'%(dudx,dudy,dvdx,dvdy)
                s += single
        if hasattr(self._offset, '__call__'):
            s += '.shift(%s)'%self._offset
        elif np.array_equal(self._offset,(0,0)):
            s += '.shift(%s,%s)'%(self._offset[0],self._offset[1])
        if hasattr(self._flux_ratio, '__call__'):
            s += '.withScaledFlux(%s)'%self._flux_ratio
        elif self._flux_ratio != 1.:
            s += '.withScaledFlux(%s)'%self._flux_ratio
        return s

    def _getTransformations(self, wave):
        if hasattr(self._jac, '__call__'):
            jac = self._jac(wave)
        else:
            jac = self._jac
        if hasattr(self._offset, '__call__'):
            offset = self._offset(wave)
        else:
            offset = self._offset
        offset = galsim.PositionD(*offset)
        if hasattr(self._flux_ratio, '__call__'):
            flux_ratio = self._flux_ratio(wave)
        else:
            flux_ratio = self._flux_ratio
        return jac, offset, flux_ratio

    def _deinterpolate(self):
        if self.interpolated:
            return galsim.ChromaticTransformation(
                    self.original.deinterpolated,
                    jac = self._jac,
                    offset = self._offset,
                    flux_ratio = self._flux_ratio,
                    gsparams = self.gsparams)
        else:
            return self

    def evaluateAtWavelength(self, wave):
        """Evaluate this chromatic object at a particular wavelength.

        @param wave     Wavelength in nanometers.

        @returns the monochromatic object at the given wavelength.
        """
        ret = self.original.evaluateAtWavelength(wave)
        jac, offset, flux_ratio = self._getTransformations(wave)
        return galsim.Transformation(ret, jac=jac, offset=offset, flux_ratio=flux_ratio,
                                     gsparams=self.gsparams)

    def drawImage(self, bandpass, image=None, integrator='trapezoidal', **kwargs):
        """
        See ChromaticObject.drawImage for a full description.

        This version usually just calls that one, but if the transformed object (self.original) is
        an InterpolatedChromaticObject, and the transformation is achromatic, then it will still be
        able to use the interpolation.

        @param bandpass         A Bandpass object representing the filter against which to
                                integrate.
        @param image            Optionally, the Image to draw onto.  (See GSObject.drawImage()
                                for details.)  [default: None]
        @param integrator       When doing the exact evaluation of the profile, this argument should
                                be one of the image integrators from galsim.integ, or a string
                                'trapezoidal' or 'midpoint', in which case the routine will use a
                                SampleIntegrator or ContinuousIntegrator depending on whether or not
                                the object has a `wave_list`.  [default: 'trapezoidal',
                                which will try to select an appropriate integrator using the
                                trapezoidal integration rule automatically.]
                                If the object being transformed is an InterpolatedChromaticObject,
                                then `integrator` can only be a string, either 'midpoint' or
                                'trapezoidal'.
        @param **kwargs         For all other kwarg options, see GSObject.drawImage()

        @returns the drawn Image.
        """
        # When drawing, we must be an SED'd object.  So check that here.
        if self.SED is None:
            raise ValueError("Can only draw ChromaticObjects with SEDs.")

        if isinstance(self.original, InterpolatedChromaticObject):
            int_im = self.original._get_interp_image(bandpass, image=image, integrator=integrator,
                                                     **kwargs)
            # Get the transformations at bandpass.red_limit (they are achromatic so it doesn't
            # matter where you get them).
            jac, offset, flux_ratio = self._getTransformations(bandpass.red_limit)
            int_im = galsim.Transform(int_im, jac=jac, offset=offset, flux_ratio=flux_ratio,
                                      gsparams=self.gsparams)
            image = int_im.drawImage(image=image, **kwargs)
            return image
        else:
            return ChromaticObject.drawImage(self, bandpass, image, integrator, **kwargs)


class ChromaticSum(ChromaticObject):
    """Add ChromaticObjects and/or GSObjects together.  If a GSObject is part of a sum, then its
    SED is assumed to be flat with spectral density of 1 photon per nanometer.

    This is the type returned from `galsim.Add(objects)` if any of the objects are a
    ChromaticObject.

    Initialization
    --------------

    Typically, you do not need to construct a ChromaticSum object explicitly.  Normally, you
    would just use the + operator, which returns a ChromaticSum when used with chromatic objects:

        >>> bulge = galsim.Sersic(n=3, half_light_radius=0.8) * bulge_sed
        >>> disk = galsim.Exponential(half_light_radius=1.4) * disk_sed
        >>> gal = bulge + disk

    You can also use the Add() factory function, which returns a ChromaticSum object if any of
    the individual objects are chromatic:

        >>> gal = galsim.Add([bulge,disk])

    @param args             Unnamed args should be a list of objects to add.
    @param gsparams         An optional GSParams argument.  See the docstring for GSParams for
                            details. [default: None]
    """
    def __init__(self, *args, **kwargs):
        # Check kwargs first:
        self.gsparams = kwargs.pop("gsparams", None)

        # Make sure there is nothing left in the dict.
        if kwargs:
            raise TypeError("Got unexpected keyword argument(s): %s"%kwargs.keys())

        if len(args) == 0:
            # No arguments. Could initialize with an empty list but draw then segfaults. Raise an
            # exception instead.
            raise ValueError("Must provide at least one GSObject or ChromaticObject.")
        elif len(args) == 1:
            # 1 argument.  Should be either a GSObject, ChromaticObject or a list of these.
            if isinstance(args[0], (galsim.GSObject, ChromaticObject)):
                args = [args[0]]
            elif isinstance(args[0], list):
                args = args[0]
            else:
                raise TypeError("Single input argument must be a GSObject, a ChromaticObject,"
                                +" or list of them.")
        # else args is already the list of objects

        self.interpolated = any(arg.interpolated for arg in args)

        # We can only add ChromaticObjects together if they're either all SED'd or all non-SED'd
        isSEDed = any(a.SED is not None for a in args)
        isNormed = any(a._norm is not None for a in args)
        if isSEDed and isNormed:
            raise ValueError("Can only add ChromaticObjects with all SEDs None or no SEDs None.")

        # Sort arguments into inseparable objects and groups of separable objects.  Note that
        # separable groups are only identified if the constituent objects have the *same* SED (or
        # *same* _norm) even though a proportional SED is mathematically sufficient for
        # separability.  It's basically impossible to identify if two SEDs are proportional (or even
        # equal) unless they point to the same memory, so we just accept this limitation.

        # Each input summand will either end up in norm_dict if it's separable, or in self.objlist
        # if it's inseparable.  Note that the keys to norm_dict can either be all SEDs or all
        # _norms, but will never be mixed.
        norm_dict = {}
        self.objlist = []
        for obj in args:
            if obj.separable:
                if isSEDed:
                    if obj.SED not in norm_dict:
                        norm_dict[obj.SED] = []
                    norm_dict[obj.SED].append(obj)
                else:
                    if obj._norm not in norm_dict:
                        norm_dict[obj._norm] = []
                    norm_dict[obj._norm].append(obj)
            else:
                self.objlist.append(obj)

        # If everything ended up in a single norm_dict entry (and self.objlist is empty) then this
        # ChromaticSum is separable.
        self.separable = (len(self.objlist) == 0 and len(norm_dict) == 1)
        if self.separable:
            the_one_norm = norm_dict.keys()[0]  # Could be either an SED or a _norm function.
            self.objlist = norm_dict[the_one_norm]
            if isSEDed:
                # Since we know that the chromatic objects' SEDs already include all relevant
                # normalizations, we can just multiply the_one_norm by the number of objects.
                self.SED = the_one_norm * len(norm_dict[the_one_norm])
                self._norm = None
            else:
                self.SED = None
                # Prefer scalar self._norm if possible.
                if hasattr(the_one_norm, '__call__'):
                    self._norm = lambda w: the_one_norm(w) * len(norm_dict[the_one_norm])
                else:
                    self._norm = the_one_norm * len(norm_dict[the_one_norm])
        else:
            # Sum is not separable, put partial sums might be.  Search for them.
            for v in norm_dict.values():
                if len(v) == 1:
                    self.objlist.append(v[0])
                else:
                    self.objlist.append(ChromaticSum(v))
            # and assemble self normalization:
            if isSEDed:
                self._norm = None
                self.SED = self.objlist[0].SED
                for obj in self.objlist[1:]:
                    self.SED += obj.SED
            else:
                self.SED = None
                # Maintain scalar type if possible.
                if any(hasattr(obj._norm, '__call__') for obj in self.objlist):
                    self._norm = lambda w: 0.0
                    for obj in self.objlist:
                        if hasattr(obj._norm, '__call__'):
                            self._norm = lambda w: self._norm(w) + obj._norm(w)
                        else:
                            self._norm = lambda w: self._norm(w) + obj._norm
                else:
                    self._norm = sum(obj._norm for obj in self.objlist)

        # finish up by constructing self.wave_list
        self.wave_list = np.array([], dtype=float)
        for obj in self.objlist:
            self.wave_list = np.union1d(self.wave_list, obj.wave_list)

    def _deinterpolate(self):
        if self.interpolated:
            return galsim.ChromaticSum([obj.deinterpolated for obj in self.objlist],
                                       gsparams=self.gsparams)
        else:
            return self

    def __eq__(self, other):
        return (isinstance(other, galsim.ChromaticSum) and
                self.objlist == other.objlist and
                self.gsparams == other.gsparams)

    def __hash__(self):
        return hash(("galsim.ChromaticSum", tuple(self.objlist), self.gsparams))

    def __repr__(self):
        return 'galsim.ChromaticSum(%r, gsparams=%r)'%(self.objlist, self.gsparams)

    def __str__(self):
        str_list = [ str(obj) for obj in self.objlist ]
        return 'galsim.ChromaticSum([%s])'%', '.join(str_list)

    def evaluateAtWavelength(self, wave):
        """Evaluate this chromatic object at a particular wavelength `wave`.

        @param wave  Wavelength in nanometers.

        @returns the monochromatic object at the given wavelength.
        """
        return galsim.Add([obj.evaluateAtWavelength(wave) for obj in self.objlist],
                          gsparams=self.gsparams)

    def drawImage(self, bandpass, image=None, integrator='trapezoidal', **kwargs):
        """Slightly optimized draw method for ChromaticSum instances.

        Draws each summand individually and add resulting images together.  This might waste time if
        two or more summands are separable and have the same SED, and another summand with a
        different SED is also added, in which case the summands should be added together first and
        the resulting Sum object can then be chromaticized.  In general, however, drawing individual
        sums independently can help with speed by identifying chromatic profiles that are separable
        into spectral and spatial factors.

        @param bandpass         A Bandpass object representing the filter against which to
                                integrate.
        @param image            Optionally, the Image to draw onto.  (See GSObject.drawImage()
                                for details.)  [default: None]
        @param integrator       When doing the exact evaluation of the profile, this argument should
                                be one of the image integrators from galsim.integ, or a string
                                'trapezoidal' or 'midpoint', in which case the routine will use a
                                SampleIntegrator or ContinuousIntegrator depending on whether or not
                                the object has a `wave_list`.  [default: 'trapezoidal',
                                which will try to select an appropriate integrator using the
                                trapezoidal integration rule automatically.]
        @param **kwargs         For all other kwarg options, see GSObject.drawImage()

        @returns the drawn Image.
        """
        # When drawing, we must be an SED'd object.  So check that here.
        if self.SED is None:
            raise ValueError("Can only draw ChromaticObjects with SEDs.")

        add_to_image = kwargs.pop('add_to_image', False)
        # Use given add_to_image for the first one, then add_to_image=False for the rest.
        image = self.objlist[0].drawImage(
                bandpass, image=image, add_to_image=add_to_image, **kwargs)
        _remove_setup_kwargs(kwargs)
        for obj in self.objlist[1:]:
            image = obj.drawImage(      bandpass, image=image, add_to_image=True, **kwargs)
        return image

    def withScaledFlux(self, flux_ratio):
        """Multiply the flux of the object by `flux_ratio`

        @param flux_ratio   The factor by which to scale the flux.

        @returns the object with the new flux.
        """
        return ChromaticSum([ obj.withScaledFlux(flux_ratio) for obj in self.objlist ])


class ChromaticConvolution(ChromaticObject):
    """Convolve ChromaticObjects and/or GSObjects together.  GSObjects are treated as having flat
    spectra.

    This is the type returned from `galsim.Convolve(objects)` if any of the objects is a
    ChromaticObject.

    Initialization
    --------------

    The normal way to use this class is to use the Convolve() factory function:

        >>> gal = galsim.Sersic(n, half_light_radius) * galsim.SED(sed_file, 'nm', 'flambda')
        >>> psf = galsim.ChromaticAtmosphere(...)
        >>> final = galsim.Convolve([gal, psf])

    The objects to be convolved may be provided either as multiple unnamed arguments (e.g.
    `Convolve(psf, gal, pix)`) or as a list (e.g. `Convolve([psf, gal, pix])`).  Any number of
    objects may be provided using either syntax.  (Well, the list has to include at least 1 item.)

    @param args             Unnamed args should be a list of objects to convolve.
    @param real_space       Whether to use real space convolution.  [default: None, which means
                            to automatically decide this according to whether the objects have hard
                            edges.]
    @param gsparams         An optional GSParams argument.  See the docstring for GSParams for
                            details. [default: None]
    """
    def __init__(self, *args, **kwargs):
        # First check for number of arguments != 0
        if len(args) == 0:
            # No arguments. Could initialize with an empty list but draw then segfaults. Raise an
            # exception instead.
            raise ValueError("Must provide at least one GSObject or ChromaticObject")
        elif len(args) == 1:
            if isinstance(args[0], (galsim.GSObject, ChromaticObject)):
                args = [args[0]]
            elif isinstance(args[0], list):
                args = args[0]
            else:
                raise TypeError(
                    "Single input argument must be a GSObject, or a ChromaticObject,"
                    +" or list of them.")
        # else args is already the list of objects

        # Check kwargs
        # real space convolution is not implemented for chromatic objects.
        real_space = kwargs.pop("real_space", None)
        if real_space:
            raise NotImplementedError(
                "Real space convolution of chromatic objects not implemented.")
        self.gsparams = kwargs.pop("gsparams", None)

        # Make sure there is nothing left in the dict.
        if kwargs:
            raise TypeError("Got unexpected keyword argument(s): %s"%kwargs.keys())

        # Accumulate convolutant .SED, and ._norm attributes.  Also make sure at most one
        # convolutant has a non-None .SED attribute.
        self.SED = None
        self._norm = 1.0
        for obj in args:
            if obj.SED is not None:
                if self.SED is None:
                    self.SED = obj.SED
                else:
                    raise ValueError("Cannot convolve multiple SED'd ChromaticObjects.")
            else: # obj.SED is None, so ._norm should not be
                @galsim.utilities.functionize
                def fn_prod(x, y):
                    return x * y
                self._norm = fn_prod(self._norm, obj._norm)
        # Finally, fold _norm into SED.
        if self.SED is not None:
            self.SED *= self._norm
            self._norm = None

        self.objlist = []
        # Unfold convolution of convolution.
        for obj in args:
            if isinstance(obj, ChromaticConvolution):
                self.objlist.extend(obj.objlist)
            else:
                self.objlist.append(obj)

        self.separable = all(obj.separable for obj in self.objlist)
        self.interpolated = any(obj.interpolated for obj in self.objlist)

        # Check quickly whether we are convolving two non-separable things that aren't
        # ChromaticSums, >1 of which uses interpolation.  If so, emit a warning that the
        # interpolation optimization is being ignored and full evaluation is necessary.
        # For the case of ChromaticSums, as long as each object in the sum is separable (even if the
        # entire object is not) then interpolation can still be used.  So we do not warn about this
        # here.
        n_nonsep = 0
        n_interp = 0
        for obj in self.objlist:
            if not obj.separable and not isinstance(obj, galsim.ChromaticSum): n_nonsep += 1
            if isinstance(obj, InterpolatedChromaticObject): n_interp += 1
        if n_nonsep>1 and n_interp>0:
            import warnings
            warnings.warn(
                "Image rendering for this convolution cannot take advantage of " +
                "interpolation-related optimization.  Will use full profile evaluation.")

        # Assemble wave_lists
        self.wave_list, _, _ = galsim.utilities.combine_wave_list(self.objlist)

    def _deinterpolate(self):
        if self.interpolated:
            return ChromaticConvolution([obj.deinterpolated for obj in self.objlist],
                                        gsparams=self.gsparams)
        else:
            return self

    @staticmethod
    def _get_effective_prof(insep_obj, bandpass, iimult, wmult, integrator, gsparams):
            # Find scale at which to draw effective profile
            _, prof0 = insep_obj._fiducial_profile(bandpass)
            iiscale = prof0.nyquistScale()
            if iimult is not None:
                iiscale /= iimult

            # Prevent infinite loop by using ChromaticObject.drawImage() on a ChromaticConvolution.

            if isinstance(insep_obj, ChromaticConvolution):
                effective_prof_image = ChromaticObject.drawImage(
                        insep_obj, bandpass, wmult=wmult, scale=iiscale,
                        integrator=integrator, method='no_pixel')
            else:
                effective_prof_image = insep_obj.drawImage(
                        bandpass, wmult=wmult, scale=iiscale, integrator=integrator,
                        method='no_pixel')

            return galsim.InterpolatedImage(effective_prof_image, gsparams=gsparams)

    @staticmethod
    def resize_effective_prof_cache(maxsize):
        """ Resize the cache containing effective profiles, (i.e., wavelength-integrated products
        of separable profile SEDs, inseparable profiles, and Bandpasses), which are used by
        ChromaticConvolution.drawImage().

        @param maxsize  The new number of effective profiles to cache.
        """
        ChromaticConvolution._effective_prof_cache.resize(maxsize)

    def __eq__(self, other):
        return (isinstance(other, galsim.ChromaticConvolution) and
                self.objlist == other.objlist and
                self.gsparams == other.gsparams)

    def __hash__(self):
        return hash(("galsim.ChromaticConvolution", tuple(self.objlist), self.gsparams))

    def __repr__(self):
        return 'galsim.ChromaticConvolution(%r, gsparams=%r)'%(self.objlist, self.gsparams)

    def __str__(self):
        str_list = [ str(obj) for obj in self.objlist ]
        return 'galsim.ChromaticConvolution([%s])'%', '.join(str_list)

    def evaluateAtWavelength(self, wave):
        """Evaluate this chromatic object at a particular wavelength `wave`.

        @param wave  Wavelength in nanometers.

        @returns the monochromatic object at the given wavelength.
        """
        return galsim.Convolve([obj.evaluateAtWavelength(wave) for obj in self.objlist],
                               gsparams=self.gsparams)

    def drawImage(self, bandpass, image=None, integrator='trapezoidal', iimult=None, **kwargs):
        """Optimized draw method for the ChromaticConvolution class.

        Works by finding sums of profiles which include separable portions, which can then be
        integrated before doing any convolutions, which are pushed to the end.

        This method uses a cache to avoid recomputing 'effective' profiles, which are the
        wavelength-integrated products of inseparable profiles, the spectral components of
        separable profiles, and the bandpass.  Because the cache size is finite, users may find
        that it is more efficient when drawing many images to group images using the same
        SEDs, bandpasses, and inseparable profiles (generally PSFs) together in order to hit the
        cache more often.  The default cache size is 10, but may be resized using the
        `ChromaticConvolution.resize_effective_prof_cache()` method.

        @param bandpass         A Bandpass object representing the filter against which to
                                integrate.
        @param image            Optionally, the Image to draw onto.  (See GSObject.drawImage()
                                for details.)  [default: None]
        @param integrator       When doing the exact evaluation of the profile, this argument should
                                be one of the image integrators from galsim.integ, or a string
                                'trapezoidal' or 'midpoint', in which case the routine will use a
                                SampleIntegrator or ContinuousIntegrator depending on whether or not
                                the object has a `wave_list`.  [default: 'trapezoidal',
                                which will try to select an appropriate integrator using the
                                trapezoidal integration rule automatically.]
        @param iimult           Oversample any intermediate InterpolatedImages created to hold
                                effective profiles by this amount. [default: None]
        @param **kwargs         For all other kwarg options, see GSObject.drawImage()

        @returns the drawn Image.
        """
        # When drawing, we must be an SED'd object.  So check that here.
        if self.SED is None:
            raise ValueError("Can only draw ChromaticObjects with SEDs.")

        # `ChromaticObject.drawImage()` can just as efficiently handle separable cases.
        if self.separable:
            return ChromaticObject.drawImage(self, bandpass, image=image, **kwargs)

        # Now split up any `ChromaticSum`s:
        # This is the tricky part.  Some notation first:
        #     int(f(x,y,lambda)) denotes the integral over wavelength of chromatic surface
        #         brightness profile f(x,y,lambda).
        #     (f1 * f2) denotes the convolution of surface brightness profiles f1 & f2.
        #     (f1 + f2) denotes the addition of surface brightness profiles f1 & f2.
        #
        # In general, chromatic s.b. profiles can be classified as either separable or inseparable,
        # depending on whether they can be factored into spatial and spectral components or not.
        # Write separable profiles as g(x,y) * h(lambda), and leave inseparable profiles as
        # f(x,y,lambda).
        # We will suppress the arguments `x`, `y`, `lambda`, hereforward, but generally an `f`
        # refers to an inseparable profile, a `g` refers to the spatial part of a separable
        # profile, and an `h` refers to the spectral part of a separable profile.
        #
        # Now, analyze a typical scenario, a bulge+disk galaxy model (each of which is separable,
        # e.g., an SED times an exponential profile for the disk, and a different SED times a DeV
        # profile for the bulge).  Suppose the PSF is inseparable.  (Chromatic PSF's will generally
        # be inseparable since we usually think of the spatial part of the PSF being normalized to
        # unit integral for any fixed wavelength.)  Say there's also an achromatic pixel to
        # convolve with.
        # The formula for this might look like:
        #
        # img = int((bulge + disk) * PSF * pix)
        #     = int((g1 h1 + g2 h2) * f3 * g4)               # note pix is lambda-independent
        #     = int(g1 h1 * f3 * g4 + g2 h2 * f3 * g4)       # distribute the + over the *
        #     = int(g1 h1 * f3 * g4) + int(g2 h2 * f3 * g4)  # distribute the + over the int
        #     = g1 * g4 * int(h1 f3) + g2 * g4 * int(h2 f3)  # move lambda-indep terms out of int
        #
        # The result is that the integral is now inside the convolution, meaning we only have to
        # compute two convolutions instead of a convolution for each wavelength at which we evaluate
        # the integrand.  This technique, making an "effective" PSF profile for each of the bulge
        # and disk, is a significant time savings in most cases.
        #
        # In general, we make effective profiles by splitting up ChromaticSum items and collecting
        # the inseparable terms on which to do integration first, and then finish with convolution
        # last.

        # Here is the logic to turn int((g1 h1 + g2 h2) * f3) -> g1 * int(h1 f3) + g2 * int(h2 f3)
        for i, obj in enumerate(self.objlist):
            if isinstance(obj, ChromaticSum):
                # say obj.objlist = [A,B,C], where obj is a ChromaticSum object
                # Assemble temporary list of convolutants excluding the ChromaticSum in question.
                tmplist = list(self.objlist)
                del tmplist[i] # remove ChromaticSum object from objlist
                tmplist.append(obj.objlist[0])  # Append first summand, i.e., A, to convolutants
                # now draw this image
                tmpobj = ChromaticConvolution(tmplist)
                add_to_image = kwargs.pop('add_to_image', False)
                image = tmpobj.drawImage(bandpass, image=image, integrator=integrator,
                                         iimult=iimult, add_to_image=add_to_image, **kwargs)
                # Now add in the rest of the summands in turn, i.e., B and C
                for summand in obj.objlist[1:]:
                    tmplist = list(self.objlist)
                    del tmplist[i]
                    tmplist.append(summand)
                    tmpobj = ChromaticConvolution(tmplist)
                    # add to previously started image
                    _remove_setup_kwargs(kwargs)
                    image = tmpobj.drawImage(bandpass, image=image, integrator=integrator,
                                             iimult=iimult, add_to_image=True, **kwargs)
                # Return the image here, breaking the loop early.  If there are two ChromaticSum
                # instances in objlist, then the above procedure will repeat in the recursion,
                # effectively distributing the multiplication over both sums.
                return image

        # If program gets this far, the objects in objlist should be atomic (non-ChromaticSum
        # and non-ChromaticConvolution).  (The latter case was dealt with in the constructor.)

        # setup output image (semi-arbitrarily using the bandpass effective wavelength)
        wave0, prof0 = self._fiducial_profile(bandpass)
        image = prof0.drawImage(image=image, setup_only=True, **kwargs)
        _remove_setup_kwargs(kwargs)

        # Sort these atomic objects into separable and inseparable lists, and collect
        # the spectral parts of the separable profiles.
        sep_profs = []
        insep_profs = []
        sep_SEDs = []
        sep_norms = []
        wave_list = np.array([], dtype=float)
        for obj in self.objlist:
            if obj.separable:
                if isinstance(obj, galsim.GSObject):
                    _norm = obj._norm
                    sep_profs.append(obj) # The g(x,y)'s (see above)
                    sep_norms.append(_norm)
                else:
                    wave0, prof0 = obj._fiducial_profile(bandpass)
                    wave_list = np.union1d(wave_list, obj.wave_list)
                    if obj.SED is not None:
                        sep_profs.append(prof0 / obj.SED(wave0)) # more g(x,y)'s
                        sep_SEDs.append(obj.SED) # The h(lambda)'s (see above)
                    else:
                        _norm = obj._norm(wave0) if hasattr(obj._norm, '__call__') else obj._norm
                        sep_profs.append(prof0 / _norm)
                        sep_norms.append(obj._norm)
            else:
                insep_profs.append(obj) # The f(x,y,lambda)'s (see above)
        # insep_profs should never be empty, since separable cases were farmed out to
        # ChromaticObject.drawImage() above.

        if len(sep_SEDs) == 0:
            sep_SED = None
        elif len(sep_SEDs) == 1:
            sep_SED = sep_SEDs[0]
        else:
            raise RuntimeError("Encountered convolution of more than one SEDed ChromaticObject.")

        wmult = kwargs.get('wmult', 1)
        # Collapse inseparable profiles and chromatic normalizations into one effective profile
        if len(insep_profs) > 1:
            insep_obj = galsim.Convolve(insep_profs, gsparams=self.gsparams)
        else:
            insep_obj = insep_profs[0]
        if sep_SED is not None:
            insep_obj *= sep_SED
        for _norm in sep_norms:
            insep_obj *= _norm
        # Note that at this point, insep_obj.SED should *not* be None.

        effective_prof = ChromaticConvolution._effective_prof_cache(
                insep_obj, bandpass, iimult, wmult,
                integrator, self.gsparams)

        # append effective profile to separable profiles (which should all be GSObjects)
        sep_profs.append(effective_prof)
        # finally, convolve and draw.
        final_prof = galsim.Convolve(sep_profs, gsparams=self.gsparams)
        return final_prof.drawImage(image=image, **kwargs)

ChromaticConvolution._effective_prof_cache = galsim.utilities.LRU_Cache(
    ChromaticConvolution._get_effective_prof, maxsize=10)


class ChromaticDeconvolution(ChromaticObject):
    """A class for deconvolving a ChromaticObject.

    The ChromaticDeconvolution class represents a wavelength-dependent deconvolution kernel.

    You may also specify a gsparams argument.  See the docstring for GSParams using
    help(galsim.GSParams) for more information about this option.  Note: if `gsparams` is
    unspecified (or None), then the ChromaticDeconvolution instance inherits the same GSParams as
    the object being deconvolved.

    Initialization
    --------------

    @param obj              The object to deconvolve.
    @param gsparams         An optional GSParams argument.  See the docstring for GSParams for
                            details. [default: None]
    """
    def __init__(self, obj, **kwargs):
        if obj.SED is not None:
            raise ValueError("Cannot deconvolve by ChromaticObject with SED.")
        self.obj = obj
        self.kwargs = kwargs
        self.separable = obj.separable
        self.interpolated = obj.interpolated
        self.SED = None
        self.wave_list = obj.wave_list
        if hasattr(obj._norm, '__call__'):
            self._norm = lambda w: 1./obj._norm(w)
        else:
            self._norm = 1./obj._norm

    def _deinterpolate(self):
        if self.interpolated:
            return ChromaticDeconvolution(self.obj.deinterpolated, **self.kwargs)
        else:
            return self

    def __eq__(self, other):
        return (isinstance(other, galsim.ChromaticDeconvolution) and
                self.obj == other.obj and
                self.kwargs == other.kwargs)

    def __hash__(self):
        return hash(("galsim.ChromaticDeconvolution", self.obj, frozenset(self.kwargs.items())))

    def __repr__(self):
        return 'galsim.ChromaticDeconvolution(%r, %r)'%(self.obj, self.kwargs)

    def __str__(self):
        return 'galsim.ChromaticDeconvolution(%s)'%self.obj

    def evaluateAtWavelength(self, wave):
        """Evaluate this chromatic object at a particular wavelength `wave`.

        @param wave  Wavelength in nanometers.

        @returns the monochromatic object at the given wavelength.
        """
        return galsim.Deconvolve(self.obj.evaluateAtWavelength(wave), **self.kwargs)


class ChromaticAutoConvolution(ChromaticObject):
    """A special class for convolving a ChromaticObject with itself.

    It is equivalent in functionality to `galsim.Convolve([obj,obj])`, but takes advantage of
    the fact that the two profiles are the same for some efficiency gains.

    Initialization
    --------------

    @param obj              The object to be convolved with itself.
    @param real_space       Whether to use real space convolution.  [default: None, which means
                            to automatically decide this according to whether the objects have hard
                            edges.]
    @param gsparams         An optional GSParams argument.  See the docstring for GSParams for
                            details. [default: None]
    """
    def __init__(self, obj, **kwargs):
        if obj.SED is not None:
            raise ValueError("Cannot autoconvolve ChromaticObject with SED.")
        self.obj = obj
        self.kwargs = kwargs
        self.separable = obj.separable
        self.interpolated = obj.interpolated
        self.SED = None
        self.wave_list = obj.wave_list
        if hasattr(obj._norm, '__call__'):
            self._norm = lambda w: obj._norm(w)**2
        else:
            self._norm = obj._norm**2

    def _deinterpolate(self):
        if self.interpolated:
            return ChromaticAutoConvolution(self.obj.deinterpolated, **self.kwargs)
        else:
            return self

    def __eq__(self, other):
        return (isinstance(other, galsim.ChromaticAutoConvolution) and
                self.obj == other.obj and
                self.kwargs == other.kwargs)

    def __hash__(self):
        return hash(("galsim.ChromaticAutoConvolution", self.obj, frozenset(self.kwargs.items())))

    def __repr__(self):
        return 'galsim.ChromaticAutoConvolution(%r, %r)'%(self.obj, self.kwargs)

    def __str__(self):
        return 'galsim.ChromaticAutoConvolution(%s)'%self.obj

    def evaluateAtWavelength(self, wave):
        """Evaluate this chromatic object at a particular wavelength `wave`.

        @param wave  Wavelength in nanometers.

        @returns the monochromatic object at the given wavelength.
        """
        return galsim.AutoConvolve(self.obj.evaluateAtWavelength(wave), **self.kwargs)


class ChromaticAutoCorrelation(ChromaticObject):
    """A special class for correlating a ChromaticObject with itself.

    It is equivalent in functionality to
        galsim.Convolve([obj,obj.rotate(180.*galsim.degrees)])
    but takes advantage of the fact that the two profiles are the same for some efficiency gains.

    Initialization
    --------------

    @param obj              The object to be convolved with itself.
    @param real_space       Whether to use real space convolution.  [default: None, which means
                            to automatically decide this according to whether the objects have hard
                            edges.]
    @param gsparams         An optional GSParams argument.  See the docstring for GSParams for
                            details. [default: None]
    """
    def __init__(self, obj, **kwargs):
        if obj.SED is not None:
            raise ValueError("Cannot autocorrelate ChromaticObject with SED.")
        self.obj = obj
        self.kwargs = kwargs
        self.separable = obj.separable
        self.interpolated = obj.interpolated
        self.SED = None
        self.wave_list = obj.wave_list
        if hasattr(obj._norm, '__call__'):
            self._norm = lambda w: obj._norm(w)**2
        else:
            self._norm = obj._norm**2

    def _deinterpolate(self):
        if self.interpolated:
            return ChromaticAutoCorrelation(self.obj.deinterpolated, **self.kwargs)
        else:
            return self

    def __eq__(self, other):
        return (isinstance(other, galsim.ChromaticAutoCorrelation) and
                self.obj == other.obj and
                self.kwargs == other.kwargs)

    def __hash__(self):
        return hash(("galsim.ChromaticAutoCorrelation", self.obj, frozenset(self.kwargs.items())))

    def __repr__(self):
        return 'galsim.ChromaticAutoCorrelation(%r, %r)'%(self.obj, self.kwargs)

    def __str__(self):
        return 'galsim.ChromaticAutoCorrelation(%s)'%self.obj

    def evaluateAtWavelength(self, wave):
        """Evaluate this chromatic object at a particular wavelength `wave`.

        @param wave  Wavelength in nanometers.

        @returns the monochromatic object at the given wavelength.
        """
        return galsim.AutoCorrelate(self.obj.evaluateAtWavelength(wave), **self.kwargs)


class ChromaticFourierSqrtProfile(ChromaticObject):
    """A class for computing the Fourier-space square root of a ChromaticObject.

    The ChromaticFourierSqrt class represents a wavelength-dependent Fourier-space square root of a profile.

    You may also specify a gsparams argument.  See the docstring for GSParams using
    help(galsim.GSParams) for more information about this option.  Note: if `gsparams` is
    unspecified (or None), then the ChromaticFourierSqrtProfile instance inherits the same GSParams as
    the object being operated on.

    Initialization
    --------------

    @param obj              The object to compute the Fourier-space square root of.
    @param gsparams         An optional GSParams argument.  See the docstring for GSParams for
                            details. [default: None]
    """
    def __init__(self, obj, **kwargs):
        import math
        if obj.SED is not None:
            raise ValueError("Cannot take fourier sqrt of ChromaticObject with SED.")
        self.obj = obj
        self.kwargs = kwargs
        self.separable = obj.separable
        self.interpolated = obj.interpolated
        self.SED = None
        self.wave_list = obj.wave_list
        if hasattr(obj._norm, '__call__'):
            self._norm = lambda w: math.sqrt(obj._norm(w))
        else:
            self._norm = math.sqrt(obj._norm)

    def _deinterpolate(self):
        if self.interpolated:
            return ChromaticFourierSqrtProfile(self.obj.deinterpolated, **self.kwargs)
        else:
            return self

    def __repr__(self):
        return 'galsim.ChromaticFourierSqrtProfile(%r, %r)'%(self.obj, self.kwargs)

    def __str__(self):
        return 'galsim.ChromaticFourierSqrtProfile(%s)'%self.obj

    def evaluateAtWavelength(self, wave):
        """Evaluate this chromatic object at a particular wavelength `wave`.

        @param wave  Wavelength in nanometers.

        @returns the monochromatic object at the given wavelength.
        """
        return galsim.FourierSqrt(self.obj.evaluateAtWavelength(wave), **self.kwargs)


class ChromaticOpticalPSF(ChromaticObject):
    """A subclass of ChromaticObject meant to represent chromatic optical PSFs.

    Chromaticity plays two roles in optical PSFs. First, it determines the diffraction limit, via
    the wavelength/diameter factor.  Second, aberrations such as defocus, coma, etc. are typically
    defined in physical distances, but their impact on the PSF depends on their size in units of
    wavelength.  Other aspects of the optical PSF do not require explicit specification of their
    chromaticity, e.g., once the obscuration and struts are specified in units of the aperture
    diameter, their chromatic dependence gets taken care of automatically.  Note that the
    ChromaticOpticalPSF implicitly defines diffraction limits in units of `scale_units`, which by
    default are arcsec, but can in principle be set to any of our GalSim angle units.

    When using interpolation to speed up image rendering (see ChromaticObject.interpolate()
    method for details), the ideal number of wavelengths to use across a given bandpass depends on
    the application and accuracy requirements.  In general it will be necessary to do a test in
    comparison with a more exact calculation to ensure convergence.  However, a typical calculation
    might use ~10-15 samples across a typical optical bandpass, with `oversample_fac` in the range
    1.5-2; for moderate accuracy, ~5 samples across the bandpass and `oversample_fac=1` may
    suffice. All of these statements assume that aberrations are not very large (typically <~0.25
    waves, which is commonly satisfied by space telescopes); if they are larger than that, then more
    stringent settings are required.

    Note that a ChromaticOpticalPSF by itself is NOT the correct thing to use to draw an image of a
    star. Stars (and galaxies too, of course) have an SED that is not flat. To draw a real star, you
    should either multiply the ChromaticOpticalPSF object by an SED, or convolve it with a point
    source (typically approximated by a very tiny Gaussian) multiplied by an SED:

        >>> psf = galsim.ChromaticOpticalPSF(...)
        >>> star = galsim.Gaussian(sigma = 1.e-6) * psf_sed
        >>> final_star = galsim.Convolve( [psf, star] )
        >>> final_star.drawImage(bandpass = bp, ...)

    @param   lam           Fiducial wavelength for which diffraction limit and aberrations are
                           initially defined, in nanometers.
    @param   diam          Telescope diameter in meters.  Either `diam` or `lam_over_diam` must be
                           specified.
    @param   lam_over_diam Ratio of (fiducial wavelength) / telescope diameter in units of
                           `scale_unit`.  Either `diam` or `lam_over_diam` must be specified.
    @param   aberrations   An array of aberrations, in units of fiducial wavelength `lam`.  The size
                           and format of this array is described in the OpticalPSF docstring.
    @param   scale_unit    Units used to define the diffraction limit and draw images.
                           [default: galsim.arcsec]
    @param   **kwargs      Any other keyword arguments to be passed to OpticalPSF, for example,
                           related to struts, obscuration, oversampling, etc.  See OpticalPSF
                           docstring for a complete list of options.
    """
    def __init__(self, lam, diam=None, lam_over_diam=None, aberrations=None,
                 scale_unit=galsim.arcsec, **kwargs):
        # First, take the basic info.
        if isinstance(scale_unit, str):
            scale_unit = galsim.angle.get_angle_unit(scale_unit)
        self.scale_unit = scale_unit

        # We have to require either diam OR lam_over_diam:
        if (diam is None and lam_over_diam is None) or \
                (diam is not None and lam_over_diam is not None):
            raise TypeError("Need to specify telescope diameter OR wavelength/diam ratio")
        if diam is not None:
            self.lam_over_diam = (1.e-9*lam/diam)*galsim.radians/self.scale_unit
            self.diam = diam
        else:
            self.lam_over_diam = lam_over_diam
            self.diam = (lam*1e-9/lam_over_diam)*galsim.radians/self.scale_unit
        self.lam = lam

        if aberrations is not None:
            self.aberrations = np.asarray(aberrations)
            if len(self.aberrations) < 12:
                self.aberrations = np.append(self.aberrations, [0] * (12-len(self.aberrations)))
        else:
            self.aberrations = np.zeros(12)
        # Pop named aberrations from kwargs so aberrations=[0,0,0,0,1] means the same as
        # defocus=1 (w/ all other named aberrations 0).
        for i, ab in enumerate(['defocus', 'astig1', 'astig2', 'coma1', 'coma2', 'trefoil1',
                                'trefoil2', 'spher']):
            if ab in kwargs:
                self.aberrations[i+4] = kwargs.pop(ab)
        self.kwargs = kwargs

        # Define the necessary attributes for this ChromaticObject.
        self.separable = False
        self.interpolated = False
        self.SED = None
        self._norm = 1.0
        self.wave_list = np.array([], dtype=float)

    def _deinterpolate(self):
        return self

    def __eq__(self, other):
        return (isinstance(other, galsim.ChromaticOpticalPSF) and
                self.lam == other.lam and
                self.lam_over_diam == other.lam_over_diam and
                np.array_equal(self.aberrations, other.aberrations) and
                self.scale_unit == other.scale_unit and
                self.kwargs == other.kwargs)

    def __hash__(self):
        return hash(("galsim.ChromaticOpticalPSF", self.lam, self.lam_over_diam,
                     tuple(self.aberrations), self.scale_unit, frozenset(self.kwargs.items())))

    def __repr__(self):
        s = 'galsim.ChromaticOpticalPSF(lam=%r, lam_over_diam=%r, aberrations=%r'%(
                self.lam, self.lam_over_diam, self.aberrations.tolist())
        if self.scale_unit != galsim.arcsec:
            s += ', scale_unit=%r'%self.scale_unit
        for k,v in self.kwargs.items():
            s += ', %s=%r'%(k,v)
        s += ')'
        return s

    def __str__(self):
        return 'galsim.ChromaticOpticalPSF(lam=%s, lam_over_diam=%s, aberrations=%s)'%(
                self.lam, self.lam_over_diam, self.aberrations.tolist())

    def evaluateAtWavelength(self, wave):
        """
        Method to directly instantiate a monochromatic instance of this object.

        @param  wave   Wavelength in nanometers.
        """
        # We need to rescale the stored lam/diam by the ratio of input wavelength to stored fiducial
        # wavelength.  Likewise, the aberrations were in units of wavelength for the fiducial
        # wavelength, so we have to convert to units of waves for *this* wavelength.
        ret = galsim.OpticalPSF(
                lam=wave, diam=self.diam,
                aberrations=self.aberrations*(self.lam/wave), scale_unit=self.scale_unit,
                **self.kwargs)
        return ret


class ChromaticAiry(ChromaticObject):
    """A subclass of ChromaticObject meant to represent chromatic Airy profiles.

    For more information about the basics of Airy profiles, please see help(galsim.Airy).

    This class is a chromatic representation of Airy profiles, including the wavelength-dependent
    diffraction limit.  One can also get this functionality using the ChromaticOpticalPSF class, but
    that class includes additional complications beyond a simple Airy profile, and thus has a more
    complicated internal representation.  For users who only want a (possibly obscured) Airy
    profile, the ChromaticAiry class is likely to be a less computationally expensive and more
    accurate option.

    @param   lam           Fiducial wavelength for which diffraction limit is initially defined, in
                           nanometers.
    @param   diam          Telescope diameter in meters.  Either `diam` or `lam_over_diam` must be
                           specified.
    @param   lam_over_diam Ratio of (fiducial wavelength) / telescope diameter in units of
                           `scale_unit`.  Either `diam` or `lam_over_diam` must be specified.
    @param   scale_unit    Units used to define the diffraction limit and draw images.
                           [default: galsim.arcsec]
    @param   **kwargs      Any other keyword arguments to be passed to Airy: either flux, or
                           gsparams.  See galsim.Airy docstring for a complete description of these
                           options.
    """
    def __init__(self, lam, diam=None, lam_over_diam=None, scale_unit=galsim.arcsec, **kwargs):
        # First, take the basic info.
        # We have to require either diam OR lam_over_diam:
        if isinstance(scale_unit, str):
            scale_unit = galsim.angle.get_angle_unit(scale_unit)
        self.scale_unit = scale_unit

        if (diam is None and lam_over_diam is None) or \
                (diam is not None and lam_over_diam is not None):
            raise TypeError("Need to specify telescope diameter OR wavelength/diam ratio")
        if diam is not None:
            self.lam_over_diam = (1.e-9*lam/diam)*galsim.radians/self.scale_unit
        else:
            self.lam_over_diam = float(lam_over_diam)
        self.lam = float(lam)

        self.kwargs = kwargs

        # Define the necessary attributes for this ChromaticObject.
        self.separable = False
        self.interpolated = False
        self.SED = None
        self._norm = 1.0
        self.wave_list = np.array([], dtype=float)

    def _deinterpolate(self):
        return self

    def __eq__(self, other):
        return (isinstance(other, galsim.ChromaticAiry) and
                self.lam == other.lam and
                self.lam_over_diam == other.lam_over_diam and
                self.scale_unit == other.scale_unit and
                self.kwargs == other.kwargs)

    def __hash__(self):
        return hash(("galsim.ChromaticAiry", self.lam, self.lam_over_diam, self.scale_unit,
                     frozenset(self.kwargs.items())))

    def __repr__(self):
        s = 'galsim.ChromaticAiry(lam=%r, lam_over_diam=%r'%(self.lam, self.lam_over_diam)
        if self.scale_unit != galsim.arcsec:
            s += ', scale_unit=%r'%self.scale_unit
        for k,v in self.kwargs.items():
            s += ', %s=%r'%(k,v)
        s += ')'
        return s

    def __str__(self):
        return 'galsim.ChromaticAiry(lam=%s, lam_over_diam=%s)'%(self.lam, self.lam_over_diam)

    def evaluateAtWavelength(self, wave):
        """
        Method to directly instantiate a monochromatic instance of this object.

        @param  wave   Wavelength in nanometers.
        """
        # We need to rescale the stored lam/diam by the ratio of input wavelength to stored fiducial
        # wavelength.
        ret = galsim.Airy(
            lam_over_diam=self.lam_over_diam*(wave/self.lam), scale_unit=self.scale_unit,
            **self.kwargs)
        return ret

def _findWave(wave_list, wave):
    """
    Helper routine to search a sorted NumPy array of wavelengths (not necessarily evenly spaced) to
    find where a particular wavelength `wave` would fit in, and return the index below along with
    the fraction of the way to the next entry in the array.
    """
    lower_idx = np.searchsorted(wave_list, wave)-1
    # There can be edge issues, so watch out for that:
    if lower_idx < 0: lower_idx = 0
    if lower_idx > len(wave_list)-1: lower_idx = len(wave_list)-1

    frac = (wave-wave_list[lower_idx]) / (wave_list[lower_idx+1]-wave_list[lower_idx])
    return lower_idx, frac

def _linearInterp(list, frac, lower_idx):
    """
    Helper routine for linear interpolation between values in lists (which could be lists of
    images, just not numbers, hence the need to avoid a LookupTable).  Not really worth
    splitting out on its own now, but could be useful to have separate routines for the
    interpolation later on if we want to enable something other than linear interpolation.
    """
    return frac*list[lower_idx+1] + (1.-frac)*list[lower_idx]

def _remove_setup_kwargs(kwargs):
    """
    Helper function to remove from kwargs anything that is only used for setting up image and that
    might otherwise interfere with drawImage.
    """
    kwargs.pop('dtype', None)
    kwargs.pop('scale', None)
    kwargs.pop('wcs', None)
    kwargs.pop('nx', None)
    kwargs.pop('ny', None)
    kwargs.pop('bounds', None)
