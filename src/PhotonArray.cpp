/* -*- c++ -*-
 * Copyright (c) 2012-2016 by the GalSim developers team on GitHub
 * https://github.com/GalSim-developers
 *
 * This file is part of GalSim: The modular galaxy image simulation toolkit.
 * https://github.com/GalSim-developers/GalSim
 *
 * GalSim is free software: redistribution and use in source and binary forms,
 * with or without modification, are permitted provided that the following
 * conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice, this
 *    list of conditions, and the disclaimer given in the accompanying LICENSE
 *    file.
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 *    this list of conditions, and the disclaimer given in the documentation
 *    and/or other materials provided with the distribution.
 */
//
// PhotonArray Class members
//

//#define DEBUGLOGGING

#include <algorithm>
#include <numeric>
#include "PhotonArray.h"

namespace galsim {

    PhotonArray::PhotonArray(
        std::vector<double>& vx, std::vector<double>& vy, std::vector<double>& vflux) :
        _is_correlated(false)
    {
        if (vx.size() != vy.size() || vx.size() != vflux.size())
            throw std::runtime_error("Size mismatch of input vectors to PhotonArray");
        _x = vx;
        _y = vy;
        _flux = vflux;
    }

    double PhotonArray::getTotalFlux() const
    {
        double total = 0.;
        return std::accumulate(_flux.begin(), _flux.end(), total);
    }

    void PhotonArray::setTotalFlux(double flux)
    {
        double oldFlux = getTotalFlux();
        if (oldFlux==0.) return; // Do nothing if the flux is zero to start with
        scaleFlux(flux / oldFlux);
    }

    void PhotonArray::scaleFlux(double scale)
    {
        for (std::vector<double>::size_type i=0; i<_flux.size(); i++) {
            _flux[i] *= scale;
        }
    }

    void PhotonArray::scaleXY(double scale)
    {
        for (std::vector<double>::size_type i=0; i<_x.size(); i++) {
            _x[i] *= scale;
        }
        for (std::vector<double>::size_type i=0; i<_y.size(); i++) {
            _y[i] *= scale;
        }
    }

    void PhotonArray::append(const PhotonArray& rhs)
    {
        if (rhs.size()==0) return;      // Nothing needed for empty RHS.
        int oldSize = size();
        int finalSize = oldSize + rhs.size();
        _x.resize(finalSize);
        _y.resize(finalSize);
        _flux.resize(finalSize);
        std::vector<double>::iterator destination=_x.begin()+oldSize;
        std::copy(rhs._x.begin(), rhs._x.end(), destination);
        destination=_y.begin()+oldSize;
        std::copy(rhs._y.begin(), rhs._y.end(), destination);
        destination=_flux.begin()+oldSize;
        std::copy(rhs._flux.begin(), rhs._flux.end(), destination);
    }

    void PhotonArray::convolve(const PhotonArray& rhs, UniformDeviate ud)
    {
        // If both arrays have correlated photons, then we need to shuffle the photons
        // as we convolve them.
        if (_is_correlated && rhs._is_correlated) return convolveShuffle(rhs,ud);

        // If neither or only one is correlated, we are ok to just use them in order.
        int N = size();
        if (rhs.size() != N)
            throw std::runtime_error("PhotonArray::convolve with unequal size arrays");
        // Add x coordinates:
        std::vector<double>::iterator lIter = _x.begin();
        std::vector<double>::const_iterator rIter = rhs._x.begin();
        for ( ; lIter!=_x.end(); ++lIter, ++rIter) *lIter += *rIter;
        // Add y coordinates:
        lIter = _y.begin();
        rIter = rhs._y.begin();
        for ( ; lIter!=_y.end(); ++lIter, ++rIter) *lIter += *rIter;
        // Multiply fluxes, with a factor of N needed:
        lIter = _flux.begin();
        rIter = rhs._flux.begin();
        for ( ; lIter!=_flux.end(); ++lIter, ++rIter) *lIter *= *rIter*N;

        // If rhs was correlated, then the output will be correlated.
        // This is ok, but we need to mark it as such.
        if (rhs._is_correlated) _is_correlated = true;
    }

    void PhotonArray::convolveShuffle(const PhotonArray& rhs, UniformDeviate ud)
    {
        int N = size();
        if (rhs.size() != N)
            throw std::runtime_error("PhotonArray::convolve with unequal size arrays");
        double xSave=0.;
        double ySave=0.;
        double fluxSave=0.;

        for (int iOut = N-1; iOut>=0; iOut--) {
            // Randomly select an input photon to use at this output
            // NB: don't need floor, since rhs is positive, so floor is superfluous.
            int iIn = int((iOut+1)*ud());
            if (iIn > iOut) iIn=iOut;  // should not happen, but be safe
            if (iIn < iOut) {
                // Save input information
                xSave = _x[iOut];
                ySave = _y[iOut];
                fluxSave = _flux[iOut];
            }
            _x[iOut] = _x[iIn] + rhs._x[iOut];
            _y[iOut] = _y[iIn] + rhs._y[iOut];
            _flux[iOut] = _flux[iIn] * rhs._flux[iOut] * N;
            if (iIn < iOut) {
                // Move saved info to new location in array
                _x[iIn] = xSave;
                _y[iIn] = ySave ;
                _flux[iIn] = fluxSave;
            }
        }
    }

    void PhotonArray::takeYFrom(const PhotonArray& rhs)
    {
        int N = size();
        assert(rhs.size()==N);
        for (int i=0; i<N; i++) {
            _y[i] = rhs._x[i];
            _flux[i] *= rhs._flux[i]*N;
        }
    }

    template <class T>
    double PhotonArray::addTo(ImageView<T> target) const
    {
        Bounds<int> b = target.getBounds();
        if (!b.isDefined())
            throw std::runtime_error("Attempting to PhotonArray::addTo an Image with"
                                     " undefined Bounds");

        double addedFlux = 0.;
        for (int i=0; i<int(size()); i++) {
            int ix = int(floor(_x[i] + 0.5));
            int iy = int(floor(_y[i] + 0.5));
            if (b.includes(ix,iy)) {
                target(ix,iy) += _flux[i];
                addedFlux += _flux[i];
            }
        }
        return addedFlux;
    }

    // instantiate template functions for expected image types
    template double PhotonArray::addTo(ImageView<float> image) const;
    template double PhotonArray::addTo(ImageView<double> image) const;
}
